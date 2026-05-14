from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

from modpack_translator.pipeline.preprocessor import (
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


def _rewrite_jar(jar_path: Path, replacements: dict[str, bytes]) -> None:
    tmp_path = jar_path.with_name(f"{jar_path.name}.tmp")
    try:
        with zipfile.ZipFile(jar_path, "r") as src, zipfile.ZipFile(tmp_path, "w") as dst:
            infos = {info.filename: info for info in src.infolist()}
            replacement_paths = set(replacements)

            for info in src.infolist():
                if info.filename in replacement_paths or _is_signature_file(info.filename):
                    continue
                dst.writestr(_clone_zip_info(info), src.read(info.filename))

            for path, data in replacements.items():
                info = _clone_zip_info(infos[path], filename=path) if path in infos else _new_zip_info(path)
                dst.writestr(info, data)

        os.replace(tmp_path, jar_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


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
    existing = read_existing_snbt(target)
    existing.update(translations)
    target.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


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
