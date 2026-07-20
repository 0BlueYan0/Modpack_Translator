from __future__ import annotations

import json
import os
import re
import shutil
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modpack_translator.pipeline import vh
from modpack_translator.pipeline.preprocessor import (
    _has_translatable_text,
    format_snbt_lang,
    parse_json_lang,
    parse_legacy_lang,
    parse_snbt_lang,
    replace_inline_snbt_text,
)


# ------------------------------------------------------------------ in-place JSON

def write_inplace_json(
    source_file: Path,
    lang_code: str,
    translations: dict[str, str],
    target_file: Path | None = None,
) -> None:
    target = target_file or source_file.parent / f"{lang_code}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    existing.update(translations)
    target.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ jar injection

_JAR_SIGNATURE_SUFFIXES = (".SF", ".DSA", ".RSA", ".EC")
_MODONOMICON_UNIFONT_PATH = "assets/modonomicon/font/include/unifont.json"
_MODONOMICON_UNIFONT_FALLBACK = {
    "providers": [
        {
            "type": "reference",
            "id": "minecraft:include/unifont",
        },
    ],
}


def backup_mods(game_root: Path) -> int:
    """Back up every mod jar once before modifying jars in place."""
    mods_dir = game_root / "mods"
    if not mods_dir.is_dir():
        return 0

    backup_dir = game_root / "mods_bak"
    backup_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for jar in sorted(mods_dir.glob("*.jar")):
        backup = backup_dir / jar.name
        if backup.exists():
            continue
        shutil.copy2(jar, backup)
        count += 1
    return count


def backup_quest_configs(game_root: Path) -> int:
    """Back up mutable quest config folders once before direct inline edits."""
    candidates = [
        game_root / "config" / "ftbquests" / "quests",
        game_root / "config" / "heracles" / "quests",
        game_root / "config" / "betterquesting",
        game_root / "kubejs" / "assets",
        # the_vault 語言表注入來源，就地翻譯值（見 vh.INPLACE_FILES）
        game_root / "config" / "the_vault" / "translations.json",
    ]
    count = 0
    backup_root = game_root / "quests_bak"
    for source in candidates:
        if not source.exists():
            continue
        destination = backup_root / source.relative_to(game_root)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        count += 1
    return count


def backup_pack_sources(game_root: Path, targets) -> int:
    """資源包寫入前備份到 packs_bak/（鏡像遊戲根目錄相對路徑,每來源一次）。

    zip 包（resourcepacks/*.zip 等,jar_inject 但不在 mods/ 下——mods 已由
    backup_mods 涵蓋）整檔複製;資料夾包只備份將被寫入的 lang 目錄,
    材質等大檔不動。"""
    backup_root = game_root / "packs_bak"
    seen: set[Path] = set()
    count = 0
    for target in targets:
        copy_dir = False
        if target.output_mode == "jar_inject":
            source = Path(target.source_file)
            if source.parent == game_root / "mods":
                continue
        elif target.format in ("pack_json_lang", "pack_legacy_lang"):
            source = Path(target.source_file).parent
            copy_dir = True
        else:
            continue
        try:
            rel = source.relative_to(game_root)
        except ValueError:
            continue
        if source in seen:
            continue
        seen.add(source)
        destination = backup_root / rel
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if copy_dir:
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        count += 1
    return count


def patch_modonomicon_unicode_fonts(game_root: Path) -> int:
    """Wire Modonomicon's empty unifont include to Minecraft's CJK-capable unifont."""
    mods_dir = game_root / "mods"
    if not mods_dir.is_dir():
        return 0

    patched = 0
    for jar_path in sorted(mods_dir.glob("*.jar")):
        if _patch_modonomicon_unicode_font(jar_path):
            patched += 1
    return patched


def _patch_modonomicon_unicode_font(jar_path: Path) -> bool:
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if _MODONOMICON_UNIFONT_PATH not in zf.namelist():
                return False
            raw = zf.read(_MODONOMICON_UNIFONT_PATH).decode("utf-8-sig")
            data = json.loads(raw)
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    if not _needs_modonomicon_unicode_font_patch(data):
        return False

    payload = json.dumps(_MODONOMICON_UNIFONT_FALLBACK, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        _rewrite_jar(jar_path, {_MODONOMICON_UNIFONT_PATH: payload})
    except OSError:
        return False
    return True


def _needs_modonomicon_unicode_font_patch(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    providers = data.get("providers")
    return isinstance(providers, list) and not providers


# ------------------------------------------------ the_vault class 常數池修補
#
# 兩類遊戲資料修不到的字串（詳見 vh.py 模組 docstring）：
# 1. Config.SUPPORTED_LOCALES 硬編碼清單無 zh_tw → config/the_vault/lang/
#    zh_tw/ 覆蓋檔永不載入。把清單中官方零資源的死 locale（es_mx）常數
#    改寫為目標語言即可讓整套覆蓋檔生效。
# 2. 選單樹（Vault Hunters Options 等）的 UI 字面值直接寫死在 class 裡。
#
# 皆為常數池 CONSTANT_Utf8 內容替換：class 檔內部一律以常數池「索引」
# 定位、無絕對位元組偏移，變長替換安全。僅替換「只被 CONSTANT_String
# 引用」的 Utf8——被 Class/NameAndType 等引用者是識別字，改了會壞。


def _parse_constant_pool(data: bytes) -> tuple[dict[int, tuple[int, bytes]], set[int], set[int], int]:
    """解析 class 常數池。回傳 (utf8 索引→(entry 起始位移, 內容 bytes),
    被 CONSTANT_String 引用的 utf8 索引集, 被其他常數引用的 utf8 索引集,
    常數池結束位移)。非 class 檔或未知 tag 拋 ValueError。"""
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("not a class file")
    count = int.from_bytes(data[8:10], "big")
    utf8: dict[int, tuple[int, bytes]] = {}
    string_refs: set[int] = set()
    other_refs: set[int] = set()
    off = 10
    i = 1
    while i < count:
        tag = data[off]
        if tag == 1:  # Utf8
            ln = int.from_bytes(data[off + 1:off + 3], "big")
            utf8[i] = (off, data[off + 3:off + 3 + ln])
            off += 3 + ln
        elif tag in (3, 4):  # Integer/Float
            off += 5
        elif tag in (5, 6):  # Long/Double（佔兩個索引槽）
            off += 9
            i += 1
        elif tag == 8:  # String → utf8
            string_refs.add(int.from_bytes(data[off + 1:off + 3], "big"))
            off += 3
        elif tag in (7, 16, 19, 20):  # Class/MethodType/Module/Package → utf8
            other_refs.add(int.from_bytes(data[off + 1:off + 3], "big"))
            off += 3
        elif tag == 12:  # NameAndType → utf8, utf8
            other_refs.add(int.from_bytes(data[off + 1:off + 3], "big"))
            other_refs.add(int.from_bytes(data[off + 3:off + 5], "big"))
            off += 5
        elif tag in (9, 10, 11, 17, 18):  # *ref/Dynamic（引用 Class/NameAndType，非 utf8）
            off += 5
        elif tag == 15:  # MethodHandle（引用 *ref）
            off += 4
        else:
            raise ValueError(f"unknown constant pool tag {tag}")
        i += 1
    return utf8, string_refs, other_refs, off


def _encode_modified_utf8(text: str) -> bytes:
    """Java modified UTF-8：BMP 非 NUL 字元與標準 UTF-8 相同；替換字串
    僅允許此子集（本工具的譯文皆為 BMP）。"""
    for ch in text:
        if ch == "\x00" or ord(ch) > 0xFFFF:
            raise ValueError(f"unsupported char in class literal: {ch!r}")
    return text.encode("utf-8")


def _replace_class_string_literals(data: bytes, replacements: dict[str, str]) -> tuple[bytes, int]:
    """把常數池中「僅被 CONSTANT_String 引用」的 Utf8 字面值換為譯文。
    回傳 (新 bytes, 替換數)；替換後重新解析驗證，失敗拋 ValueError。"""
    utf8, string_refs, other_refs, _ = _parse_constant_pool(data)
    wanted = {key.encode("utf-8"): value for key, value in replacements.items()}
    hits: list[tuple[int, bytes, bytes]] = []  # (entry 位移, 原 bytes, 新 bytes)
    for idx, (entry_off, raw) in utf8.items():
        new_text = wanted.get(raw)
        if new_text is None:
            continue
        if idx not in string_refs or idx in other_refs:
            continue
        hits.append((entry_off, raw, _encode_modified_utf8(new_text)))
    if not hits:
        return data, 0
    out = bytearray(data)
    for entry_off, raw, new_bytes in sorted(hits, reverse=True):
        out[entry_off + 1:entry_off + 3 + len(raw)] = (
            len(new_bytes).to_bytes(2, "big") + new_bytes
        )
    patched = bytes(out)
    _parse_constant_pool(patched)  # 驗證：改壞寧可拋錯也不寫入
    return patched, len(hits)


@dataclass
class VaultPatchPlan:
    jar_path: Path
    replacements: dict[str, bytes] = field(default_factory=dict)
    locale_patched: bool = False
    literal_count: int = 0


def find_vault_jar(game_root: Path) -> Path | None:
    mods_dir = game_root / "mods"
    if not mods_dir.is_dir():
        return None
    for jar in sorted(mods_dir.glob("*.jar")):
        if jar.name.lower().startswith("the_vault"):
            return jar
    return None


def plan_vault_class_patch(game_root: Path, lang_code: str) -> VaultPatchPlan | None:
    """算出 the_vault jar 需要的 class 修補（不寫入）。無事可做回傳 None。
    解析失敗的 class 一律跳過（未來 VH 版本結構變動時寧可少修不誤修）。"""
    jar_path = find_vault_jar(game_root)
    if jar_path is None:
        return None
    locale = lang_code.lower()
    plan = VaultPatchPlan(jar_path=jar_path)
    try:
        with zipfile.ZipFile(jar_path) as zf:
            names = set(zf.namelist())
            if vh.CONFIG_CLASS_PATH in names and locale != vh.LOCALE_PATCH_DONOR:
                try:
                    raw = zf.read(vh.CONFIG_CLASS_PATH)
                    utf8, _, _, _ = _parse_constant_pool(raw)
                    values = {payload for _, payload in utf8.values()}
                    if locale.encode("ascii") not in values:  # 已支援/已修補則略過
                        patched, n = _replace_class_string_literals(
                            raw, {vh.LOCALE_PATCH_DONOR: locale}
                        )
                        if n:
                            plan.replacements[vh.CONFIG_CLASS_PATH] = patched
                            plan.locale_patched = True
                except ValueError:
                    pass
            if locale == "zh_tw":
                for cls, mapping in vh.HARDCODED_UI_LITERALS.items():
                    if cls not in names:
                        continue
                    try:
                        patched, n = _replace_class_string_literals(zf.read(cls), mapping)
                    except ValueError:
                        continue
                    if n:
                        plan.replacements[cls] = patched
                        plan.literal_count += n
    except (zipfile.BadZipFile, OSError):
        return None
    return plan if plan.replacements else None


def apply_vault_class_patch(plan: VaultPatchPlan) -> None:
    _rewrite_jar(plan.jar_path, plan.replacements)


# 顯示文句啟發式：首字大寫、至少兩個以空白分隔的 ASCII 詞（含空白的字串
# 不可能是 Java 識別字/描述子/資源 ID）。\x01 是 invokedynamic 字串串接
# 槽位。單詞顯示字串（Rendering/Color…）風險較高，只走人工白名單。
_DISPLAY_LITERAL_RE = re.compile(r"^[A-Z][ -~\x01]*( [ -~\x01]+)+$")
_VAULT_LITERAL_CLASS_PREFIXES = ("iskallia/vault/client/", "iskallia/vault/mixin/")


def _is_display_literal(s: str) -> bool:
    if not (4 <= len(s) <= 200):
        return False
    if "://" in s or s.startswith(("gui/", "textures/", "the_vault")):
        return False
    # 字面大括號幾乎必為 toString 樣板（"TextureAtlasRegion{\x01, \x01}"），
    # 非 UI 顯示文句（串接槽位是 \x01 不是大括號）
    if "{" in s or "}" in s:
        return False
    if not _DISPLAY_LITERAL_RE.match(s):
        return False
    # 無可翻譯內容者（"LVL \x01"：縮寫＋槽位）送翻只會原樣返回被接受，
    # 修補為相同 bytes 後每輪重新入列空轉——直接不收
    return _has_translatable_text(s.replace("\x01", " ").strip())


def extract_vault_ui_literals(jar_path: Path) -> dict[str, list[str]]:
    """列出 the_vault client/mixin class 內待翻的顯示字面值。

    僅收「只被 CONSTANT_String 引用」且符合顯示文句啟發式的多詞 ASCII
    字串（已翻譯者含 CJK、不符 ASCII 條件 → 自然冪等）；人工白名單
    HARDCODED_UI_LITERALS 已涵蓋的字串交給 plan_vault_class_patch。"""
    result: dict[str, list[str]] = {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            for name in zf.namelist():
                if not name.endswith(".class"):
                    continue
                if not name.startswith(_VAULT_LITERAL_CLASS_PREFIXES):
                    continue
                curated = vh.HARDCODED_UI_LITERALS.get(name, {})
                try:
                    utf8, string_refs, other_refs, _ = _parse_constant_pool(zf.read(name))
                except ValueError:
                    continue
                out: list[str] = []
                for idx, (_, raw) in utf8.items():
                    if idx not in string_refs or idx in other_refs:
                        continue
                    try:
                        text = raw.decode("ascii")
                    except UnicodeDecodeError:
                        continue  # 非 ASCII（含已翻中文）
                    if text in curated:
                        continue
                    if _is_display_literal(text):
                        out.append(text)
                if out:
                    result[name] = out
    except (zipfile.BadZipFile, OSError):
        return {}
    return result


def patch_vault_literal_map(jar_path: Path, mapping: dict[str, dict[str, str]]) -> int:
    """依 class→{原文: 譯文} 改寫 jar 常數池，回傳實際替換數。"""
    replacements: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(jar_path) as zf:
        names = set(zf.namelist())
        for cls, m in mapping.items():
            if cls not in names or not m:
                continue
            try:
                patched, n = _replace_class_string_literals(zf.read(cls), m)
            except ValueError:
                continue
            if n:
                replacements[cls] = patched
                total += n
    if replacements:
        _rewrite_jar(jar_path, replacements)
    return total


def read_jar_json_lang(jar_path: Path, path_in_jar: str | None) -> dict[str, str]:
    if not path_in_jar:
        return {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if path_in_jar not in zf.namelist():
                return {}
            return parse_json_lang(zf.read(path_in_jar).decode("utf-8-sig"))
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def read_jar_legacy_lang(jar_path: Path, path_in_jar: str | None) -> dict[str, str]:
    if not path_in_jar:
        return {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if path_in_jar not in zf.namelist():
                return {}
            return parse_legacy_lang(zf.read(path_in_jar).decode("utf-8-sig"))
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError):
        return {}


def read_jar_json_file(jar_path: Path, path_in_jar: str | None) -> dict[str, Any]:
    if not path_in_jar:
        return {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if path_in_jar not in zf.namelist():
                return {}
            data = json.loads(zf.read(path_in_jar).decode("utf-8-sig"))
            return data if isinstance(data, dict) else {}
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def write_jar_json_lang(jar_path: Path, path_in_jar: str, translations: dict[str, str]) -> None:
    existing = read_jar_json_lang(jar_path, path_in_jar)
    existing.update(translations)
    payload = json.dumps(existing, ensure_ascii=False, indent=2).encode("utf-8")
    _rewrite_jar(jar_path, {path_in_jar: payload})


def write_jar_legacy_lang(jar_path: Path, path_in_jar: str, translations: dict[str, str]) -> None:
    existing = read_jar_legacy_lang(jar_path, path_in_jar)
    existing.update(translations)
    payload = ("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n").encode("utf-8")
    _rewrite_jar(jar_path, {path_in_jar: payload})


def write_jar_json_file(jar_path: Path, path_in_jar: str, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _rewrite_jar(jar_path, {path_in_jar: raw})


def write_jar_text(jar_path: Path, path_in_jar: str, text: str) -> None:
    _rewrite_jar(jar_path, {path_in_jar: text.encode("utf-8")})


def _rewrite_jar(jar_path: Path, replacements: dict[str, bytes]) -> None:
    tmp_path = jar_path.with_name(f"{jar_path.name}.tmp")
    try:
        with zipfile.ZipFile(jar_path, "r") as src, zipfile.ZipFile(tmp_path, "w") as dst:
            infos = {info.filename: info for info in src.infolist()}
            replacement_paths = set(replacements)
            written: set[str] = set()

            for info in src.infolist():
                if info.filename in replacement_paths or _is_signature_file(info.filename):
                    continue
                # 有些 jar（如 ars_nouveau）的 central directory 對同一 entry 有
                # 多筆重複記錄，重寫時只保留第一筆
                if info.filename in written:
                    continue
                written.add(info.filename)
                dst.writestr(_clone_zip_info(info), _read_jar_entry(src, info))

            for path, data in replacements.items():
                info = _clone_zip_info(infos[path], filename=path) if path in infos else _new_zip_info(path)
                dst.writestr(info, data)

        os.replace(tmp_path, jar_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _read_jar_entry(src: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """讀取 entry 內容，容忍重複 central directory 記錄。

    多筆記錄指向同一個 local header 時，CPython 的 zip-bomb 防護會誤判
    Overlapped entries 而拒讀；此時依 local header 直接讀原始位元組解壓，
    並以 CRC 驗證資料完好。"""
    try:
        return src.read(info.filename)
    except zipfile.BadZipFile:
        data = _read_jar_entry_raw(src, info)
        if zlib.crc32(data) & 0xFFFFFFFF != info.CRC:
            raise
        return data


def _read_jar_entry_raw(src: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    fp = src.fp
    fp.seek(info.header_offset)
    header = fp.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise zipfile.BadZipFile(f"Bad local header for {info.filename!r}")
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    fp.seek(info.header_offset + 30 + name_len + extra_len)
    payload = fp.read(info.compress_size)
    if info.compress_type == zipfile.ZIP_STORED:
        return payload
    if info.compress_type == zipfile.ZIP_DEFLATED:
        return zlib.decompress(payload, -15)
    raise zipfile.BadZipFile(
        f"Unsupported compression {info.compress_type} for {info.filename!r}"
    )


def _is_signature_file(path_in_jar: str) -> bool:
    upper = path_in_jar.upper()
    return upper.startswith("META-INF/") and upper.endswith(_JAR_SIGNATURE_SUFFIXES)


def _clone_zip_info(info: zipfile.ZipInfo, filename: str | None = None) -> zipfile.ZipInfo:
    cloned = zipfile.ZipInfo(filename or info.filename, date_time=info.date_time)
    cloned.comment = info.comment
    cloned.extra = info.extra
    cloned.internal_attr = info.internal_attr
    cloned.external_attr = info.external_attr
    cloned.create_system = info.create_system
    cloned.compress_type = info.compress_type
    return cloned


def _new_zip_info(path_in_jar: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path_in_jar)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


# ------------------------------------------------------------------ in-place SNBT

def read_existing_snbt(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_snbt_lang(path.read_text(encoding="utf-8"))


def write_inplace_snbt(
    source_file: Path,
    lang_code: str,
    translations: dict[str, str],
    target_file: Path | None = None,
) -> None:
    target = target_file or source_file.parent / f"{lang_code}.snbt"
    target.parent.mkdir(parents=True, exist_ok=True)
    source_values = _read_snbt_source(source_file)
    existing = read_existing_snbt(target)
    if source_values:
        existing = {key: value for key, value in existing.items() if key in source_values}
        translations = {key: value for key, value in translations.items() if key in source_values}
    merged = dict(source_values)
    merged.update(existing)
    merged.update(translations)
    ordered = _ordered_snbt_lang(source_file, merged)
    target.write_text(format_snbt_lang(ordered), encoding="utf-8")


def _read_snbt_source(source_file: Path) -> dict[str, str]:
    try:
        return parse_snbt_lang(source_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def _ordered_snbt_lang(source_file: Path, values: dict[str, str]) -> dict[str, str]:
    ordered: dict[str, str] = {}
    source = _read_snbt_source(source_file)

    for key in source:
        if key in values:
            ordered[key] = values[key]
    for key, value in values.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


# ------------------------------------------------------------------ in-place legacy .lang

def read_existing_bq_lang(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_legacy_lang(path.read_text(encoding="utf-8"))


def write_inplace_bq_lang(
    source_file: Path,
    lang_code: str,
    translations: dict[str, str],
    target_file: Path | None = None,
) -> None:
    target = target_file or source_file.parent / f"{lang_code}.lang"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = read_existing_bq_lang(target)
    existing.update(translations)
    lines = [f"{k}={v}" for k, v in existing.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_inline_snbt(source_file: Path, translations: dict[str, str]) -> None:
    raw = source_file.read_text(encoding="utf-8")
    updated = replace_inline_snbt_text(raw, translations)
    if updated != raw:
        source_file.write_text(updated, encoding="utf-8")
