from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from modpack_translator.pipeline.patcher import (
    read_jar_json_file,
    read_jar_json_lang,
    read_jar_legacy_lang,
    read_existing_bq_lang,
    read_existing_snbt,
    write_inplace_bq_lang,
    write_inplace_json,
    write_inplace_snbt,
    write_inline_snbt,
    write_jar_json_file,
    write_jar_json_lang,
    write_jar_legacy_lang,
)
from modpack_translator.pipeline.postprocessor import process
from modpack_translator.pipeline.preprocessor import (
    PATCHOULI_TEXT_FIELDS,
    decode,
    diff_keys,
    encode,
    read_inline_snbt_text,
    read_legacy_lang,
    read_bq_lang,
    read_json_lang,
    read_patchouli_page,
    read_snbt_lang,
)
from modpack_translator.pipeline.scanner import TranslationTarget


def cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


# 用來偵測「有無可翻譯的真實字母內容」
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")


def _translate_single(
    translator: Any,
    encoded: str,
    tokens: list[str],
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    """嘗試翻譯，失敗時最多重試 retry_count 次。cancel_check() 為 True 時立即中止。

    快速路徑：若移除 {N} 佔位符後沒有任何英文字母，表示字串本身無可翻譯內容
    （如 "[{2}]"），直接還原 tokens 並回傳原文，避免模型推理浪費資源且必然失敗。
    """
    # 移除 {N} 佔位符後若無任何英文字母，表示無可翻譯內容（如 "[{2}]"）
    # 直接 decode 還原 token 並回傳，不需推理且不會失敗
    if not _HAS_LETTER_RE.search(re.sub(r"\{[0-9]+\}", "", encoded)):
        return decode(encoded, tokens), True

    for _ in range(1 + retry_count):
        if cancel_check is not None and cancel_check():
            return encoded, False
        raw = translator.translate(encoded, cancel_check=cancel_check)
        final, ok = process(raw, encoded, tokens)
        if ok:
            return final, True
    return encoded, False


def translate_dict(
    en_dict: dict[str, str],
    zh_existing: dict[str, str],
    translator: Any,
    cache: dict[str, str],
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
) -> tuple[dict[str, str], int, int, int, dict[str, str]]:
    """翻譯缺少/未翻譯的鍵值。回傳 (result, translated, cached, fallback, failed)。"""
    to_translate = diff_keys(en_dict, zh_existing)
    result: dict[str, str] = {}
    failed: dict[str, str] = {}
    n_translated = n_cached = n_fallback = 0

    for key in to_translate:
        if cancel_check is not None and cancel_check():
            break
        src = en_dict[key]
        ck = cache_key(src)
        if ck in cache:
            result[key] = cache[ck]
            n_cached += 1
            if on_pair_done is not None:
                on_pair_done(1)
            continue
        encoded, tokens = encode(src)
        final, ok = _translate_single(translator, encoded, tokens, retry_count, cancel_check)
        if ok:
            result[key] = final
            cache[ck] = final
            n_translated += 1
        else:
            result[key] = src
            failed[key] = src
            n_fallback += 1
        if on_pair_done is not None:
            on_pair_done(1)

    return result, n_translated, n_cached, n_fallback, failed


def read_target_strings(target: TranslationTarget) -> dict[str, str]:
    if target.format == "json_lang":
        return read_json_lang(target.source_file, target.path_in_jar)
    elif target.format == "legacy_lang":
        return read_legacy_lang(target.source_file, target.path_in_jar)
    elif target.format == "patchouli_json":
        page = read_patchouli_page(target.source_file, target.path_in_jar)
        return {f: page[f] for f in PATCHOULI_TEXT_FIELDS if f in page and isinstance(page[f], str)}
    elif target.format in ("ftbq_snbt", "heracles_snbt"):
        return read_snbt_lang(target.source_file)
    elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
        return read_inline_snbt_text(target.source_file)
    elif target.format == "bq_lang":
        return read_bq_lang(target.source_file)
    elif target.format == "kubejs_json":
        return read_json_lang(target.source_file, None)
    return {}


def read_existing_target(target: TranslationTarget, lang_code: str) -> dict[str, str]:
    if target.output_mode == "jar_inject":
        if target.format == "json_lang":
            return read_jar_json_lang(target.source_file, target.target_path_in_jar)
        if target.format == "legacy_lang":
            return read_jar_legacy_lang(target.source_file, target.target_path_in_jar)
        if target.format == "patchouli_json":
            page = read_jar_json_file(target.source_file, target.target_path_in_jar)
            return {
                f: page[f]
                for f in PATCHOULI_TEXT_FIELDS
                if f in page and isinstance(page[f], str)
            }
        return {}

    if target.format in ("ftbq_snbt", "heracles_snbt"):
        path = target.target_file or target.source_file.parent / f"{lang_code}.snbt"
        return read_existing_snbt(path)
    elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
        return {}
    elif target.format == "bq_lang":
        path = target.target_file or target.source_file.parent / f"{lang_code}.lang"
        return read_existing_bq_lang(path)
    else:
        path = target.target_file or target.source_file.parent / f"{lang_code}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def process_target(
    target: TranslationTarget,
    translator: Any,
    cache: dict[str, str],
    lang_code: str,
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
) -> tuple[int, int, int, dict[str, str]]:
    """處理單一翻譯目標。回傳 (translated, cached, fallback, failed)。"""
    if target.format == "patchouli_json":
        return _process_patchouli(target, translator, cache, retry_count, cancel_check, on_pair_done)

    if target.format == "json_lang":
        en_dict = read_json_lang(target.source_file, target.path_in_jar)
    elif target.format == "legacy_lang":
        en_dict = read_legacy_lang(target.source_file, target.path_in_jar)
    elif target.format in ("ftbq_snbt", "heracles_snbt"):
        en_dict = read_snbt_lang(target.source_file)
    elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
        en_dict = read_inline_snbt_text(target.source_file)
    elif target.format == "bq_lang":
        en_dict = read_bq_lang(target.source_file)
    elif target.format == "kubejs_json":
        en_dict = read_json_lang(target.source_file, None)
    else:
        return 0, 0, 0, {}

    if not en_dict:
        return 0, 0, 0, {}

    zh_existing = read_existing_target(target, lang_code)
    result, n_translated, n_cached, n_fallback, failed = translate_dict(
        en_dict, zh_existing, translator, cache, retry_count, cancel_check, on_pair_done
    )

    if result:
        if target.output_mode == "jar_inject":
            if not target.target_path_in_jar:
                raise ValueError(f"Missing jar target path for {target.source_file}")
            if target.format == "json_lang":
                write_jar_json_lang(target.source_file, target.target_path_in_jar, result)
            elif target.format == "legacy_lang":
                write_jar_legacy_lang(target.source_file, target.target_path_in_jar, result)
            else:
                raise ValueError(f"Unsupported jar injection format: {target.format}")
        elif target.format in ("ftbq_snbt", "heracles_snbt"):
            write_inplace_snbt(target.source_file, lang_code, result, target.target_file)
        elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
            write_inline_snbt(target.source_file, result)
        elif target.format == "bq_lang":
            write_inplace_bq_lang(target.source_file, lang_code, result, target.target_file)
        else:
            write_inplace_json(target.source_file, lang_code, result, target.target_file)

    return n_translated, n_cached, n_fallback, failed


def _process_patchouli(
    target: TranslationTarget,
    translator: Any,
    cache: dict[str, str],
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
) -> tuple[int, int, int, dict[str, str]]:
    if not target.path_in_jar:
        raise ValueError(f"Missing Patchouli source path for {target.source_file}")

    source_page = read_patchouli_page(target.source_file, target.path_in_jar)
    target_path = target.target_path_in_jar or target.path_in_jar
    if not target_path:
        raise ValueError(f"Missing Patchouli target path for {target.source_file}")
    page = read_jar_json_file(target.source_file, target_path) if target.output_mode == "jar_inject" else {}
    if not page:
        page = dict(source_page)

    source_strings = {
        field: source_page[field]
        for field in PATCHOULI_TEXT_FIELDS
        if field in source_page and isinstance(source_page[field], str)
    }
    existing_strings = {
        field: page[field]
        for field in PATCHOULI_TEXT_FIELDS
        if field in page and isinstance(page[field], str)
    }
    to_translate = diff_keys(source_strings, existing_strings)

    changed = False
    failed: dict[str, str] = {}
    n_translated = n_cached = n_fallback = 0

    for field in to_translate:
        if cancel_check is not None and cancel_check():
            break
        src = source_strings[field]
        ck = cache_key(src)
        if ck in cache:
            page[field] = cache[ck]
            changed = True
            n_cached += 1
            continue
        encoded, tokens = encode(src)
        final, ok = _translate_single(translator, encoded, tokens, retry_count, cancel_check)
        if ok:
            page[field] = final
            cache[ck] = final
            changed = True
            n_translated += 1
        else:
            failed[field] = src
            n_fallback += 1
        if on_pair_done is not None:
            on_pair_done(1)

    if changed:
        if target.output_mode != "jar_inject":
            raise ValueError("Patchouli resource pack output is no longer supported")
        write_jar_json_file(target.source_file, target_path, page)

    return n_translated, n_cached, n_fallback, failed


def _write_failed_items(
    failed_by_target: dict[str, dict[str, str]],
    output_dir: Path,
) -> int:
    """將失敗項目分檔寫入 output_dir。無失敗項目時不建立資料夾，回傳 0。"""
    total_failed = sum(len(v) for v in failed_by_target.values())
    if total_failed == 0:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for target_name, items in sorted(failed_by_target.items()):
        if not items:
            continue
        safe_name = target_name.replace("/", "_").replace("\\", "_")
        file_path = output_dir / f"{safe_name}.txt"
        lines = [
            f"失敗項目清單：{target_name}",
            f"失敗數量：{len(items)} 個",
            "",
        ]
        for key, src in sorted(items.items()):
            display_src = src[:200] + "…" if len(src) > 200 else src
            lines.append(f"  {key}")
            lines.append(f'    原文："{display_src}"')
            lines.append("")
        file_path.write_text("\n".join(lines), encoding="utf-8")
        written += 1
    return written
