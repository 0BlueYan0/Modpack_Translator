from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from modpack_translator.pipeline import mdx
from modpack_translator.pipeline.preprocessor import (
    diff_keys,
    parse_json_lang,
    parse_legacy_lang,
    parse_snbt_lang,
    read_patchouli_text,
    read_inline_snbt_text,
)


@dataclass
class TranslationTarget:
    source_file: Path
    path_in_jar: str | None
    mod_id: str
    format: str       # json_lang | legacy_lang | patchouli_json | ftbq_snbt | ftbq_inline_snbt | heracles_snbt | heracles_inline_snbt | bq_lang | kubejs_json | oracle_mdx | oracle_meta | guideme_md
    output_mode: str  # jar_inject | in_place
    output_lang_code: str = "zh_tw"
    target_path_in_jar: str | None = None      # 寫入目標:一律正規小寫（遊戲讀得到）
    target_file: Path | None = None            # 寫入目標:一律正規小寫
    existing_path_in_jar: str | None = None    # 既有譯檔（可能大寫 zh_TW），供重用/diff
    existing_file: Path | None = None          # 既有譯檔（可能大寫），供重用/diff


def resolve_game_root(path: Path) -> Path:
    """Detect the actual Minecraft game root inside various launcher structures."""
    # Prism Launcher (CurseForge pack import): instance_dir/minecraft/
    if (path / "minecraft").is_dir():
        return path / "minecraft"

    # Prism Launcher / MultiMC (manual pack): instance_dir/.minecraft/
    if (path / ".minecraft").is_dir():
        return path / ".minecraft"

    # GDLauncher: instance_dir/files/
    if (path / "files" / "mods").is_dir():
        return path / "files"

    # CurseForge App / ATLauncher / FTB App / manual: use path directly
    return path


class ModpackScanner:
    def scan(self, modpack_path: Path, lang_code: str = "zh_tw", glossary=None,
             include_translated: bool = False) -> list[TranslationTarget]:
        """掃描待翻譯目標。include_translated=True 時停用「已翻譯就跳過」的過濾，
        回傳所有含來源字串的目標（供建立 hash→英文 sidecar 用，不用於翻譯流程）。"""
        root = self._resolve_game_root(modpack_path)
        print(f"Detected game root: {root}")

        self._include_translated = include_translated
        try:
            targets: list[TranslationTarget] = []

            mods_dir = root / "mods"
            if mods_dir.is_dir():
                for jar in sorted(mods_dir.glob("*.jar")):
                    targets.extend(self._scan_jar(jar, lang_code, glossary))

            targets.extend(self._scan_ftbquests(root, lang_code, glossary))
            targets.extend(self._scan_heracles(root, lang_code, glossary))
            targets.extend(self._scan_betterquesting(root, lang_code, glossary))
            targets.extend(self._scan_kubejs(root, lang_code, glossary))

            return targets
        finally:
            self._include_translated = False

    def _resolve_game_root(self, path: Path) -> Path:
        return resolve_game_root(path)

    # ------------------------------------------------------------------ jars

    def _scan_jar(self, jar_path: Path, lang_code: str, glossary=None) -> list[TranslationTarget]:
        targets: list[TranslationTarget] = []
        guideme_root_cache: dict[str, bool] = {}
        try:
            with zipfile.ZipFile(jar_path) as zf:
                names = zf.namelist()
                name_set = set(names)
                for name in names:
                    parts = name.split("/")
                    lang_ext = self._source_lang_extension(parts)
                    if lang_ext:
                        mod_id = parts[1]
                        existing_path = self._existing_lang_path(name, lang_code, name_set)
                        write_path = self._canonical_lang_path(name, lang_code)
                        needs = self._jar_lang_needs_translation(zf, name, existing_path, lang_ext, glossary)
                        if needs or self._needs_case_migration(existing_path, write_path, name_set):
                            targets.append(TranslationTarget(
                                source_file=jar_path,
                                path_in_jar=name,
                                mod_id=mod_id,
                                format="json_lang" if lang_ext == "json" else "legacy_lang",
                                output_mode="jar_inject",
                                output_lang_code=lang_code,
                                target_path_in_jar=write_path,
                                existing_path_in_jar=existing_path,
                            ))

                    elif (
                        len(parts) >= 3
                        and parts[0] == "assets"
                        and "patchouli_books" in parts
                        and name.endswith(".json")
                        and not name.endswith("/")
                    ):
                        write_path = self._canonical_patchouli_path(parts, lang_code)
                        if not write_path:
                            continue
                        existing_path = self._existing_patchouli_path(parts, lang_code, name_set)
                        mod_id = parts[1]
                        needs = self._patchouli_needs_translation(zf, name, existing_path, glossary)
                        if needs or self._needs_case_migration(existing_path, write_path, name_set):
                            targets.append(TranslationTarget(
                                source_file=jar_path,
                                path_in_jar=name,
                                mod_id=mod_id,
                                format="patchouli_json",
                                output_mode="jar_inject",
                                output_lang_code=lang_code,
                                target_path_in_jar=write_path,
                                existing_path_in_jar=existing_path,
                            ))

                    else:
                        target = self._scan_oracle_book(zf, jar_path, name, parts, name_set, lang_code, glossary)
                        if target is None:
                            target = self._scan_guideme_page(
                                zf, jar_path, name, parts, name_set, lang_code, glossary, guideme_root_cache
                            )
                        if target is not None:
                            targets.append(target)
        except (zipfile.BadZipFile, OSError):
            pass
        return targets

    def _source_lang_extension(self, parts: list[str]) -> str | None:
        if len(parts) != 4 or parts[0] != "assets" or parts[2] != "lang":
            return None
        filename = parts[3]
        lower = filename.lower()
        if lower == "en_us.json":
            return "json"
        if lower == "en_us.lang":
            return "lang"
        return None

    def _canonical_lang_path(self, source_path: str, lang_code: str) -> str:
        """寫入目標:一律正規小寫檔名。Minecraft 語言碼為小寫、jar 內查找
        (ZipFile.getEntry) 區分大小寫,只有 zh_tw.json 會被遊戲載入。"""
        lang_dir, filename = source_path.rsplit("/", 1)
        ext = filename.rsplit(".", 1)[1]
        return f"{lang_dir}/{lang_code.lower()}.{ext}"

    def _existing_lang_path(self, source_path: str, lang_code: str, names: set[str]) -> str | None:
        """既有譯檔(供 diff/重用)。優先小寫,其次大寫變體;皆無則 None。"""
        lang_dir, filename = source_path.rsplit("/", 1)
        ext = filename.rsplit(".", 1)[1]
        for candidate in self._lang_code_candidates(lang_code, ext):
            path = f"{lang_dir}/{candidate}"
            if path in names:
                return path
        return None

    def _needs_case_migration(
        self, existing_path: str | None, write_path: str, names: set[str]
    ) -> bool:
        """既有譯檔在非正規(大寫)路徑,而正規小寫路徑尚不存在 → 需遷移。
        即使無新內容待翻,也要把既有譯文複製到遊戲讀得到的小寫檔。"""
        return existing_path is not None and existing_path != write_path and write_path not in names

    def _lang_code_candidates(self, lang_code: str, ext: str) -> list[str]:
        lower = lang_code.lower()
        candidates = [f"{lower}.{ext}"]
        if "_" in lower:
            left, right = lower.split("_", 1)
            candidates.append(f"{left}_{right.upper()}.{ext}")
        return list(dict.fromkeys(candidates))

    def _jar_lang_needs_translation(
        self,
        zf: zipfile.ZipFile,
        source_path: str,
        existing_path: str | None,
        lang_ext: str,
        glossary=None,
    ) -> bool:
        if getattr(self, "_include_translated", False):
            return True
        try:
            source_raw = zf.read(source_path).decode("utf-8-sig")
            source = parse_json_lang(source_raw) if lang_ext == "json" else parse_legacy_lang(source_raw)
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not source:
            return False

        existing: dict[str, str] = {}
        if existing_path and existing_path in zf.namelist():
            try:
                target_raw = zf.read(existing_path).decode("utf-8-sig")
                existing = parse_json_lang(target_raw) if lang_ext == "json" else parse_legacy_lang(target_raw)
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                existing = {}
        return bool(diff_keys(source, existing, glossary=glossary))

    def _canonical_patchouli_path(self, parts: list[str], lang_code: str) -> str | None:
        """寫入目標:語系目錄一律正規小寫(en_us → zh_tw)。"""
        idx = next((i for i, part in enumerate(parts) if part.lower() == "en_us"), None)
        if idx is None:
            return None
        target_parts = list(parts)
        target_parts[idx] = lang_code.lower()
        return "/".join(target_parts)

    def _existing_patchouli_path(self, parts: list[str], lang_code: str, names: set[str]) -> str | None:
        """既有譯頁(可能在大寫語系目錄),供 diff/重用;皆無則 None。"""
        idx = next((i for i, part in enumerate(parts) if part.lower() == "en_us"), None)
        if idx is None:
            return None
        for candidate in self._locale_candidates(lang_code):
            target_parts = list(parts)
            target_parts[idx] = candidate
            target_path = "/".join(target_parts)
            if target_path in names:
                return target_path
        return None

    def _patchouli_needs_translation(self, zf: zipfile.ZipFile, source_path: str, existing_path: str | None, glossary=None) -> bool:
        if getattr(self, "_include_translated", False):
            return True
        try:
            source_page = json.loads(zf.read(source_path).decode("utf-8-sig"))
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            return False

        source = read_patchouli_text(source_page)
        if not source:
            return False

        existing: dict[str, str] = {}
        if existing_path and existing_path in zf.namelist():
            try:
                target_page = json.loads(zf.read(existing_path).decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                target_page = {}
            existing = read_patchouli_text(target_page)
        return bool(diff_keys(source, existing, glossary=glossary))

    # ------------------------------------------------------------ Oracle wiki

    def _scan_oracle_book(self, zf, jar_path, name, parts, name_set, lang_code, glossary) -> TranslationTarget | None:
        # 路徑：assets/oracle_index/books/<book>/<root>/...；root∈{content,docs}。
        # 輸出/既有譯樹是 .../books/<book>/translated/<locale>/<root>/... —
        # 其 parts[4]=="translated" 已被下方 root 判定排除，故不需再全域搜尋 "translated"
        # （全域搜尋會誤殺 book id 或子資料夾剛好叫 translated 的合法內容）。
        if not (len(parts) >= 6 and parts[0] == "assets" and parts[1] == "oracle_index"
                and parts[2] == "books" and parts[4] in ("content", "docs")):
            return None
        book = parts[3]
        root_idx = 4
        write = "/".join(parts[:root_idx] + ["translated", lang_code.lower()] + parts[root_idx:])
        existing = write if write in name_set else None

        if name.endswith(".mdx"):
            if not self._mdx_needs_translation(zf, name, existing, glossary):
                return None
            return TranslationTarget(
                source_file=jar_path, path_in_jar=name, mod_id=book,
                format="oracle_mdx", output_mode="jar_inject",
                output_lang_code=lang_code,
                target_path_in_jar=write, existing_path_in_jar=existing,
            )
        if parts[-1] == "_meta.json":
            if not self._oracle_meta_needs_translation(zf, name, existing, glossary):
                return None
            return TranslationTarget(
                source_file=jar_path, path_in_jar=name, mod_id=book,
                format="oracle_meta", output_mode="jar_inject",
                output_lang_code=lang_code,
                target_path_in_jar=write, existing_path_in_jar=existing,
            )
        return None

    def _mdx_needs_translation(self, zf, source_path, existing_path, glossary) -> bool:
        """MDX/GuideME md 頁需翻判定:來源可翻段 diff 既有譯頁後非空。oracle 與 guideme 共用。"""
        if getattr(self, "_include_translated", False):
            return True
        try:
            source = mdx.extract_mdx(zf.read(source_path).decode("utf-8-sig"))
        except (KeyError, UnicodeDecodeError):
            return False
        if not source:
            return False
        existing = {}
        if existing_path and existing_path in zf.namelist():
            try:
                existing = mdx.extract_mdx(zf.read(existing_path).decode("utf-8-sig"))
            except (KeyError, UnicodeDecodeError):
                existing = {}
        return bool(diff_keys(source, existing, glossary=glossary))

    # ------------------------------------------------------------ GuideME 指南

    # 既有翻譯樹目錄:_fr_fr、_zh_cn、_zh_tw…(GuideME LangUtil 的 "_"+語言碼 慣例)
    _GUIDEME_LANG_DIR_RE = re.compile(r"^_[a-z]{2,3}_[a-z]{2,4}$")
    # frontmatter 區塊(--- … ---)與其中的頂層 navigation: 鍵(GuideME 頁面專屬慣例)
    _GUIDEME_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)---[ \t]*\r?\n", re.S)
    _GUIDEME_NAV_KEY_RE = re.compile(r"^navigation:[ \t]*\r?$", re.M)

    def _scan_guideme_page(
        self, zf, jar_path, name, parts, name_set, lang_code, glossary,
        root_cache: dict[str, bool],
    ) -> TranslationTarget | None:
        """GuideME 指南頁(AE2 按 G 指南等)。頁面是 jar 內 assets/<ns>/<root>/**.md,
        由 GuideME 依遊戲語言載入 <root>/_<lang>/<相同相對路徑> 的譯頁、逐頁 fallback 英文。
        root 三種形態:assets/<ns>/guides/<a>/<b>(預設佈局)、assets/<ns>/<folder>(自訂,
        如 ae2guide/guide)。以「root 子樹內存在 navigation frontmatter」資格審查,
        排除 credits/README/lang 目錄等雜訊 .md。"""
        if not name.endswith(".md"):
            return None
        root_parts = self._guideme_root_parts(parts)
        if root_parts is None:
            return None
        rel_parts = parts[len(root_parts):]
        if not rel_parts or not rel_parts[-1]:
            return None
        if any(self._GUIDEME_LANG_DIR_RE.match(p) for p in rel_parts[:-1]):
            return None  # 既有翻譯樹(含我們寫出的 _zh_tw)不是來源:冪等
        root = "/".join(root_parts)
        if not self._guideme_root_qualified(zf, root, name_set, root_cache):
            return None
        rel = "/".join(rel_parts)
        write = f"{root}/_{lang_code.lower()}/{rel}"
        existing = next(
            (f"{root}/_{cand}/{rel}" for cand in self._locale_candidates(lang_code)
             if f"{root}/_{cand}/{rel}" in name_set),
            None,
        )
        if not self._mdx_needs_translation(zf, name, existing, glossary):
            return None
        return TranslationTarget(
            source_file=jar_path, path_in_jar=name, mod_id=parts[1],
            format="guideme_md", output_mode="jar_inject",
            output_lang_code=lang_code,
            target_path_in_jar=write, existing_path_in_jar=existing,
        )

    @staticmethod
    def _guideme_root_parts(parts: list[str]) -> list[str] | None:
        """指南 root。guides 預設佈局取三層(guides/<ns>/<name>),否則取首層資料夾;
        assets 直下的散檔與層數不足的 guides/ 無法安全定位 → None。"""
        if len(parts) < 4 or parts[0] != "assets":
            return None
        if parts[2] == "guides":
            return parts[:5] if len(parts) >= 6 else None
        return parts[:3]

    def _guideme_root_qualified(self, zf, root: str, name_set, cache: dict[str, bool]) -> bool:
        if root in cache:
            return cache[root]
        prefix = root + "/"
        ok = False
        for candidate in name_set:
            if not (candidate.startswith(prefix) and candidate.endswith(".md")):
                continue
            try:
                raw = zf.read(candidate).decode("utf-8-sig")
            except (KeyError, UnicodeDecodeError):
                continue
            m = self._GUIDEME_FM_RE.match(raw)
            if m and self._GUIDEME_NAV_KEY_RE.search(m.group(1)):
                ok = True
                break
        cache[root] = ok
        return ok

    def _oracle_meta_needs_translation(self, zf, source_path, existing_path, glossary) -> bool:
        if getattr(self, "_include_translated", False):
            return True
        try:
            source = mdx.extract_meta(zf.read(source_path).decode("utf-8-sig"))
        except (KeyError, UnicodeDecodeError):
            return False
        if not source:
            return False
        existing = {}
        if existing_path and existing_path in zf.namelist():
            try:
                existing = mdx.extract_meta(zf.read(existing_path).decode("utf-8-sig"))
            except (KeyError, UnicodeDecodeError):
                existing = {}
        return bool(diff_keys(source, existing, glossary=glossary))

    # ---------------------------------------------------------- local lang files

    def _is_source_locale_name(self, name: str, target_lang: str) -> bool:
        normalized = self._normalize_locale(name)
        target = self._normalize_locale(target_lang)
        if normalized == target:
            return False
        return normalized == "en_us" or normalized.startswith("en_") or normalized == "en"

    def _is_locale_like_name(self, name: str) -> bool:
        normalized = self._normalize_locale(name)
        return bool(re.fullmatch(r"[a-z]{2,3}(?:_[a-z]{2,3})?", normalized))

    def _normalize_locale(self, value: str) -> str:
        stem = Path(value).stem
        return stem.replace("-", "_").lower()

    def _is_ignored_lang_path(self, path: Path) -> bool:
        ignored_parts = {"recovery", "__pycache__"}
        if any(part.lower() in ignored_parts for part in path.parts):
            return True
        return path.name.endswith(".snbt_merged") or path.name.endswith(".bak")

    def _looks_english_like_file(self, path: Path, parser) -> bool:
        try:
            values = [v.strip() for v in parser(path.read_text(encoding="utf-8")).values() if v.strip()]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not values:
            return False

        sample = values[:50]
        englishish = sum(1 for value in sample if re.search(r"[A-Za-z]", value))
        cjk = sum(1 for value in sample if re.search(r"[\u3400-\u9fff]", value))
        return englishish >= max(1, len(sample) // 3) and cjk <= max(1, len(sample) // 4)

    def _scan_file_has_pending_text(self, source_file: Path, existing_file: Path | None, parser, glossary=None) -> bool:
        if getattr(self, "_include_translated", False):
            return True
        try:
            source = parser(source_file.read_text(encoding="utf-8"))
            existing = (
                parser(existing_file.read_text(encoding="utf-8"))
                if existing_file and existing_file.exists()
                else {}
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return bool(diff_keys(source, existing, glossary=glossary))

    def _canonical_flat_locale_file(self, source_file: Path, lang_code: str) -> Path:
        """寫入目標:一律正規小寫檔名。"""
        return source_file.with_name(f"{lang_code.lower()}{source_file.suffix}")

    def _existing_flat_locale_file(self, source_file: Path, lang_code: str) -> Path | None:
        for locale in self._locale_candidates(lang_code):
            candidate = source_file.with_name(f"{locale}{source_file.suffix}")
            if candidate.exists():
                return candidate
        return None

    def _canonical_split_locale_file(self, lang_root: Path, source_file: Path, lang_code: str) -> Path:
        """寫入目標:語系目錄一律正規小寫。"""
        parts = list(source_file.relative_to(lang_root).parts)
        parts[0] = lang_code.lower()
        return lang_root.joinpath(*parts)

    def _existing_split_locale_file(self, lang_root: Path, source_file: Path, lang_code: str) -> Path | None:
        parts = list(source_file.relative_to(lang_root).parts)
        for locale in self._locale_candidates(lang_code):
            candidate = lang_root.joinpath(locale, *parts[1:])
            if candidate.exists():
                return candidate
        return None

    def _needs_local_migration(self, existing_file: Path | None, write_file: Path) -> bool:
        """既有譯檔在非正規(大寫)路徑,而正規小寫路徑尚不存在 → 需遷移。
        (Windows 檔名不分大小寫時 write_file.exists() 會命中大寫檔,自動不遷移。)"""
        return existing_file is not None and existing_file != write_file and not write_file.exists()

    def _locale_candidates(self, lang_code: str) -> list[str]:
        lower = lang_code.lower()
        candidates = [lower]
        if "_" in lower:
            left, right = lower.split("_", 1)
            candidates.append(f"{left}_{right.upper()}")
        return list(dict.fromkeys(candidates))

    def _scan_snbt_lang_tree(self, lang_root: Path, mod_id: str, fmt: str, lang_code: str, glossary=None) -> list[TranslationTarget]:
        if not lang_root.is_dir():
            return []

        targets: list[TranslationTarget] = []
        for lang_file in sorted(lang_root.rglob("*.snbt")):
            if self._is_ignored_lang_path(lang_file):
                continue

            relative = lang_file.relative_to(lang_root)
            parts = relative.parts
            if len(parts) == 1:
                locale_name = lang_file.stem
                existing_file = self._existing_flat_locale_file(lang_file, lang_code)
                write_file = self._canonical_flat_locale_file(lang_file, lang_code)
            else:
                locale_name = parts[0]
                existing_file = self._existing_split_locale_file(lang_root, lang_file, lang_code)
                write_file = self._canonical_split_locale_file(lang_root, lang_file, lang_code)

            if not self._is_source_locale_name(locale_name, lang_code):
                if self._is_locale_like_name(locale_name) or not self._looks_english_like_file(lang_file, parse_snbt_lang):
                    continue

            needs = self._scan_file_has_pending_text(lang_file, existing_file, parse_snbt_lang, glossary)
            if not (needs or self._needs_local_migration(existing_file, write_file)):
                continue

            targets.append(TranslationTarget(
                source_file=lang_file,
                path_in_jar=None,
                mod_id=mod_id,
                format=fmt,
                output_mode="in_place",
                output_lang_code=lang_code,
                target_file=write_file,
                existing_file=existing_file,
            ))
        return targets

    def _scan_lang_files(self, root: Path, mod_id: str, fmt: str, suffix: str, parser, lang_code: str, glossary=None) -> list[TranslationTarget]:
        if not root.is_dir():
            return []

        targets: list[TranslationTarget] = []
        for lang_file in sorted(root.rglob(f"*{suffix}")):
            if self._is_ignored_lang_path(lang_file):
                continue
            locale_name = lang_file.stem
            if not self._is_source_locale_name(locale_name, lang_code):
                if self._is_locale_like_name(locale_name) or not self._looks_english_like_file(lang_file, parser):
                    continue

            existing_file = self._existing_flat_locale_file(lang_file, lang_code)
            write_file = self._canonical_flat_locale_file(lang_file, lang_code)
            needs = self._scan_file_has_pending_text(lang_file, existing_file, parser, glossary)
            if not (needs or self._needs_local_migration(existing_file, write_file)):
                continue
            targets.append(TranslationTarget(
                source_file=lang_file,
                path_in_jar=None,
                mod_id=mod_id,
                format=fmt,
                output_mode="in_place",
                output_lang_code=lang_code,
                target_file=write_file,
                existing_file=existing_file,
            ))
        return targets

    # 任務 lang 檔的鍵形如 quest.0000A88BB40B2149.title。誤放到 chapters/ 等
    # 結構目錄的 lang 檔不會被遊戲讀取，且其內容已由 lang/ 樹處理，需跳過
    # 以免 inline 掃描把整份鍵值當成未翻譯的內文重翻一遍。
    _SNBT_LANG_KEY_LINE_RE = re.compile(
        r"^\s*(?:chapter|chapter_group|quest|task|reward|reward_table|loot_crate|file)"
        r"\.[0-9A-Fa-f]{16}\.[A-Za-z_]+\s*:",
        re.MULTILINE,
    )

    def _looks_like_snbt_lang_file(self, raw: str) -> bool:
        return len(self._SNBT_LANG_KEY_LINE_RE.findall(raw)) >= 2

    def _scan_inline_snbt_files(self, root: Path, mod_id: str, fmt: str) -> list[TranslationTarget]:
        if not root.is_dir():
            return []

        skip_dirs = {"lang", "data", "progress", "recovery"}
        targets: list[TranslationTarget] = []
        for source_file in sorted(root.rglob("*.snbt")):
            relative_parts = {part.lower() for part in source_file.relative_to(root).parts[:-1]}
            if relative_parts & skip_dirs:
                continue
            if self._is_ignored_lang_path(source_file):
                continue
            try:
                raw = source_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if self._looks_like_snbt_lang_file(raw):
                continue
            try:
                strings = read_inline_snbt_text(source_file)
            except (OSError, UnicodeDecodeError):
                continue
            if not strings:
                continue
            targets.append(TranslationTarget(
                source_file=source_file,
                path_in_jar=None,
                mod_id=mod_id,
                format=fmt,
                output_mode="in_place",
            ))
        return targets

    # --------------------------------------------------------------- FTB Quests

    def _scan_ftbquests(self, modpack_path: Path, lang_code: str, glossary=None) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "ftbquests"
        if not config_dir.is_dir():
            return []
        quests_dir = config_dir / "quests"
        targets = self._scan_snbt_lang_tree(quests_dir / "lang", "ftbquests", "ftbq_snbt", lang_code, glossary)
        targets.extend(self._scan_inline_snbt_files(quests_dir, "ftbquests", "ftbq_inline_snbt"))
        return targets

    # --------------------------------------------------------------- Heracles (Odyssey Quests)

    def _scan_heracles(self, modpack_path: Path, lang_code: str, glossary=None) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "heracles"
        if not config_dir.is_dir():
            return []
        quests_dir = config_dir / "quests"
        targets = self._scan_snbt_lang_tree(quests_dir / "lang", "heracles", "heracles_snbt", lang_code, glossary)
        targets.extend(self._scan_inline_snbt_files(quests_dir, "heracles", "heracles_inline_snbt"))
        return targets

    # --------------------------------------------------------------- Better Questing (1.12.x)

    def _scan_betterquesting(self, modpack_path: Path, lang_code: str, glossary=None) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "betterquesting"
        return self._scan_lang_files(config_dir, "betterquesting", "bq_lang", ".lang", parse_legacy_lang, lang_code, glossary)

    # --------------------------------------------------------------- KubeJS lang

    def _scan_kubejs(self, modpack_path: Path, lang_code: str, glossary=None) -> list[TranslationTarget]:
        assets_dir = modpack_path / "kubejs" / "assets"
        if not assets_dir.is_dir():
            return []
        targets: list[TranslationTarget] = []
        for lang_dir in sorted(assets_dir.glob("*/lang")):
            if lang_dir.is_dir():
                namespace = lang_dir.parent.name
                targets.extend(self._scan_lang_files(lang_dir, namespace, "kubejs_json", ".json", parse_json_lang, lang_code, glossary))
        return targets
