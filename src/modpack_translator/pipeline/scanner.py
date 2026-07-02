from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

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
    format: str       # json_lang | legacy_lang | patchouli_json | ftbq_snbt | ftbq_inline_snbt | heracles_snbt | heracles_inline_snbt | bq_lang | kubejs_json
    output_mode: str  # jar_inject | in_place
    output_lang_code: str = "zh_tw"
    target_path_in_jar: str | None = None
    target_file: Path | None = None


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
    def scan(self, modpack_path: Path, lang_code: str = "zh_tw") -> list[TranslationTarget]:
        root = self._resolve_game_root(modpack_path)
        print(f"Detected game root: {root}")

        targets: list[TranslationTarget] = []

        mods_dir = root / "mods"
        if mods_dir.is_dir():
            for jar in sorted(mods_dir.glob("*.jar")):
                targets.extend(self._scan_jar(jar, lang_code))

        targets.extend(self._scan_ftbquests(root, lang_code))
        targets.extend(self._scan_heracles(root, lang_code))
        targets.extend(self._scan_betterquesting(root, lang_code))
        targets.extend(self._scan_kubejs(root, lang_code))

        return targets

    def _resolve_game_root(self, path: Path) -> Path:
        return resolve_game_root(path)

    # ------------------------------------------------------------------ jars

    def _scan_jar(self, jar_path: Path, lang_code: str) -> list[TranslationTarget]:
        targets: list[TranslationTarget] = []
        try:
            with zipfile.ZipFile(jar_path) as zf:
                names = zf.namelist()
                name_set = set(names)
                for name in names:
                    parts = name.split("/")
                    lang_ext = self._source_lang_extension(parts)
                    if lang_ext:
                        mod_id = parts[1]
                        target_path = self._target_lang_path(name, lang_code, name_set)
                        if self._jar_lang_needs_translation(zf, name, target_path, lang_ext):
                            targets.append(TranslationTarget(
                                source_file=jar_path,
                                path_in_jar=name,
                                mod_id=mod_id,
                                format="json_lang" if lang_ext == "json" else "legacy_lang",
                                output_mode="jar_inject",
                                output_lang_code=lang_code,
                                target_path_in_jar=target_path,
                            ))

                    elif (
                        len(parts) >= 3
                        and parts[0] == "assets"
                        and "patchouli_books" in parts
                        and name.endswith(".json")
                        and not name.endswith("/")
                    ):
                        target_path = self._target_patchouli_path(parts, lang_code, name_set)
                        if not target_path:
                            continue
                        mod_id = parts[1]
                        if self._patchouli_needs_translation(zf, name, target_path):
                            targets.append(TranslationTarget(
                                source_file=jar_path,
                                path_in_jar=name,
                                mod_id=mod_id,
                                format="patchouli_json",
                                output_mode="jar_inject",
                                output_lang_code=lang_code,
                                target_path_in_jar=target_path,
                            ))
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

    def _target_lang_path(self, source_path: str, lang_code: str, names: set[str]) -> str:
        lang_dir, filename = source_path.rsplit("/", 1)
        ext = filename.rsplit(".", 1)[1]
        candidates = self._lang_code_candidates(lang_code, ext)
        for candidate in candidates:
            path = f"{lang_dir}/{candidate}"
            if path in names:
                return path
        return f"{lang_dir}/{lang_code.lower()}.{ext}"

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
        target_path: str,
        lang_ext: str,
    ) -> bool:
        try:
            source_raw = zf.read(source_path).decode("utf-8-sig")
            source = parse_json_lang(source_raw) if lang_ext == "json" else parse_legacy_lang(source_raw)
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not source:
            return False

        existing: dict[str, str] = {}
        if target_path in zf.namelist():
            try:
                target_raw = zf.read(target_path).decode("utf-8-sig")
                existing = parse_json_lang(target_raw) if lang_ext == "json" else parse_legacy_lang(target_raw)
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                existing = {}
        return bool(diff_keys(source, existing))

    def _target_patchouli_path(self, parts: list[str], lang_code: str, names: set[str]) -> str | None:
        source_locale_idx = next(
            (i for i, part in enumerate(parts) if part.lower() == "en_us"),
            None,
        )
        if source_locale_idx is None:
            return None

        candidates = [lang_code.lower()]
        if "_" in lang_code:
            left, right = lang_code.lower().split("_", 1)
            candidates.append(f"{left}_{right.upper()}")

        for candidate in dict.fromkeys(candidates):
            target_parts = list(parts)
            target_parts[source_locale_idx] = candidate
            target_path = "/".join(target_parts)
            if target_path in names:
                return target_path

        target_parts = list(parts)
        target_parts[source_locale_idx] = lang_code.lower()
        return "/".join(target_parts)

    def _patchouli_needs_translation(self, zf: zipfile.ZipFile, source_path: str, target_path: str) -> bool:
        try:
            source_page = json.loads(zf.read(source_path).decode("utf-8-sig"))
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            return False

        source = read_patchouli_text(source_page)
        if not source:
            return False

        existing: dict[str, str] = {}
        if target_path in zf.namelist():
            try:
                target_page = json.loads(zf.read(target_path).decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                target_page = {}
            existing = read_patchouli_text(target_page)
        return bool(diff_keys(source, existing))

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

    def _scan_file_has_pending_text(self, source_file: Path, target_file: Path, parser) -> bool:
        try:
            source = parser(source_file.read_text(encoding="utf-8"))
            existing = parser(target_file.read_text(encoding="utf-8")) if target_file.exists() else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return bool(diff_keys(source, existing))

    def _target_flat_locale_file(self, source_file: Path, lang_code: str) -> Path:
        for locale in self._locale_candidates(lang_code):
            candidate = source_file.with_name(f"{locale}{source_file.suffix}")
            if candidate.exists():
                return candidate
        return source_file.with_name(f"{lang_code.lower()}{source_file.suffix}")

    def _target_split_locale_file(self, lang_root: Path, source_file: Path, lang_code: str) -> Path:
        relative = source_file.relative_to(lang_root)
        parts = list(relative.parts)
        parts[0] = self._existing_locale_dir_name(lang_root, lang_code)
        return lang_root.joinpath(*parts)

    def _locale_candidates(self, lang_code: str) -> list[str]:
        lower = lang_code.lower()
        candidates = [lower]
        if "_" in lower:
            left, right = lower.split("_", 1)
            candidates.append(f"{left}_{right.upper()}")
        return list(dict.fromkeys(candidates))

    def _existing_locale_dir_name(self, lang_root: Path, lang_code: str) -> str:
        for locale in self._locale_candidates(lang_code):
            if (lang_root / locale).is_dir():
                return locale
        return lang_code.lower()

    def _scan_snbt_lang_tree(self, lang_root: Path, mod_id: str, fmt: str, lang_code: str) -> list[TranslationTarget]:
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
                target_file = self._target_flat_locale_file(lang_file, lang_code)
            else:
                locale_name = parts[0]
                target_file = self._target_split_locale_file(lang_root, lang_file, lang_code)

            if not self._is_source_locale_name(locale_name, lang_code):
                if self._is_locale_like_name(locale_name) or not self._looks_english_like_file(lang_file, parse_snbt_lang):
                    continue

            if not self._scan_file_has_pending_text(lang_file, target_file, parse_snbt_lang):
                continue

            targets.append(TranslationTarget(
                source_file=lang_file,
                path_in_jar=None,
                mod_id=mod_id,
                format=fmt,
                output_mode="in_place",
                output_lang_code=lang_code,
                target_file=target_file,
            ))
        return targets

    def _scan_lang_files(self, root: Path, mod_id: str, fmt: str, suffix: str, parser, lang_code: str) -> list[TranslationTarget]:
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

            target_file = self._target_flat_locale_file(lang_file, lang_code)
            if not self._scan_file_has_pending_text(lang_file, target_file, parser):
                continue
            targets.append(TranslationTarget(
                source_file=lang_file,
                path_in_jar=None,
                mod_id=mod_id,
                format=fmt,
                output_mode="in_place",
                output_lang_code=lang_code,
                target_file=target_file,
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

    def _scan_ftbquests(self, modpack_path: Path, lang_code: str) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "ftbquests"
        if not config_dir.is_dir():
            return []
        quests_dir = config_dir / "quests"
        targets = self._scan_snbt_lang_tree(quests_dir / "lang", "ftbquests", "ftbq_snbt", lang_code)
        targets.extend(self._scan_inline_snbt_files(quests_dir, "ftbquests", "ftbq_inline_snbt"))
        return targets

    # --------------------------------------------------------------- Heracles (Odyssey Quests)

    def _scan_heracles(self, modpack_path: Path, lang_code: str) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "heracles"
        if not config_dir.is_dir():
            return []
        quests_dir = config_dir / "quests"
        targets = self._scan_snbt_lang_tree(quests_dir / "lang", "heracles", "heracles_snbt", lang_code)
        targets.extend(self._scan_inline_snbt_files(quests_dir, "heracles", "heracles_inline_snbt"))
        return targets

    # --------------------------------------------------------------- Better Questing (1.12.x)

    def _scan_betterquesting(self, modpack_path: Path, lang_code: str) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "betterquesting"
        return self._scan_lang_files(config_dir, "betterquesting", "bq_lang", ".lang", parse_legacy_lang, lang_code)

    # --------------------------------------------------------------- KubeJS lang

    def _scan_kubejs(self, modpack_path: Path, lang_code: str) -> list[TranslationTarget]:
        assets_dir = modpack_path / "kubejs" / "assets"
        if not assets_dir.is_dir():
            return []
        targets: list[TranslationTarget] = []
        for lang_dir in sorted(assets_dir.glob("*/lang")):
            if lang_dir.is_dir():
                namespace = lang_dir.parent.name
                targets.extend(self._scan_lang_files(lang_dir, namespace, "kubejs_json", ".json", parse_json_lang, lang_code))
        return targets
