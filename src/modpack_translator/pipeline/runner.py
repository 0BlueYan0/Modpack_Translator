from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
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
    classify_translation_entry,
    decode,
    diff_keys,
    encode,
    read_inline_snbt_text,
    is_usable_translation,
    read_legacy_lang,
    read_bq_lang,
    read_json_lang,
    read_patchouli_page,
    read_patchouli_text,
    read_snbt_lang,
    write_patchouli_text,
)
from modpack_translator.pipeline.scanner import TranslationTarget


def cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


# 用來偵測「有無可翻譯的真實字母內容」
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_PATCHOULI_SEGMENT_SPLIT_RE = re.compile(r"(\$\((?:p|br2?|li\d*)\)|\\?@[A-Z][A-Z0-9_]*@)")
_GENERIC_SEGMENT_SPLIT_RE = re.compile(r"(\r?\n+|\\?@(?:L|PAGE)@)")
_SENTENCE_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?])(\s+)")
_STATIC_TRANSLATIONS = {
    "Bosses": "首領",
    "Cat": "貓",
    "Chicken": "雞",
    "Cow": "牛",
    "Pig": "豬",
    "Sheep": "綿羊",
    "Villager": "村民",
}
_STATIC_PATTERNS: tuple[tuple[re.Pattern[str], dict[str, str], str], ...] = (
    (
        re.compile(r"^(%s) Pacifies (Endermen|Phantoms|Piglins) when worn$"),
        {"Endermen": "終界使者", "Phantoms": "夜魅", "Piglins": "豬布林"},
        "{0} 穿戴時會安撫{1}",
    ),
)


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


def _translate_validated(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    static = _static_translation(source)
    if static is not None and is_usable_translation(source, static):
        return static, True

    # 整串正好是官方詞彙：直接用官方譯名，不呼叫模型（語義同靜態表短路）
    glossary = getattr(translator, "glossary", None)
    if glossary is not None:
        official = glossary.exact_match(source)
        if official is not None and is_usable_translation(source, official):
            return official, True

    encoded, tokens = encode(source)
    final, ok = _translate_single(translator, encoded, tokens, retry_count, cancel_check)
    # 模型輸出關卡開啟專有名詞豁免：模型對模組名、人名等原樣返回是正確判斷
    if ok and is_usable_translation(source, final, accept_identical_proper_noun=True):
        return final, True
    return source, False


def _static_translation(source: str) -> str | None:
    text = source.strip()
    if text in _STATIC_TRANSLATIONS:
        return source.replace(text, _STATIC_TRANSLATIONS[text])
    for pattern, mapping, template in _STATIC_PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        translated = template.format(match.group(1), mapping[match.group(2)])
        return source.replace(text, translated)
    return None


def _translate_segmented_text(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    final, ok = _translate_validated(translator, source, retry_count, cancel_check)
    if ok:
        return final, True

    parts = _GENERIC_SEGMENT_SPLIT_RE.split(source)
    if len(parts) <= 1:
        return _translate_sentence_segmented_text(translator, source, retry_count, cancel_check)

    translated_parts: list[str] = []
    changed = False
    for part in parts:
        if not part:
            continue
        if _GENERIC_SEGMENT_SPLIT_RE.fullmatch(part):
            translated_parts.append(part)
            continue
        if not part.strip():
            translated_parts.append(part)
            continue
        part_final, part_ok = _translate_validated(translator, part, retry_count, cancel_check)
        if not part_ok:
            return source, False
        translated_parts.append(part_final)
        changed = changed or part_final != part

    combined = "".join(translated_parts)
    if not changed:
        # 每個內容段都各自通過驗證且原樣即正確（如模組名列表），整串照原樣接受
        return source, True
    if is_usable_translation(source, combined):
        return combined, True
    final, ok = _translate_sentence_segmented_text(translator, source, retry_count, cancel_check)
    if ok:
        return final, True
    return source, False


def _translate_sentence_segmented_text(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    if len(source) < 120:
        return source, False
    parts = _SENTENCE_SEGMENT_SPLIT_RE.split(source)
    if len(parts) <= 1:
        return source, False

    translated_parts: list[str] = []
    changed = False
    for part in parts:
        if not part:
            continue
        if _SENTENCE_SEGMENT_SPLIT_RE.fullmatch(part) or not part.strip():
            translated_parts.append(part)
            continue
        part_final, part_ok = _translate_validated(translator, part, retry_count, cancel_check)
        if not part_ok:
            return source, False
        translated_parts.append(part_final)
        changed = changed or part_final != part

    combined = "".join(translated_parts)
    if not changed:
        return source, True
    if is_usable_translation(source, combined):
        return combined, True
    return source, False


def _translate_patchouli_text(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    final, ok = _translate_segmented_text(translator, source, retry_count, cancel_check)
    if ok:
        return final, True

    parts = _PATCHOULI_SEGMENT_SPLIT_RE.split(source)
    if len(parts) <= 1:
        return source, False

    translated_parts: list[str] = []
    changed = False
    for part in parts:
        if not part:
            continue
        if _PATCHOULI_SEGMENT_SPLIT_RE.fullmatch(part):
            translated_parts.append(part)
            continue
        part_final, part_ok = _translate_segmented_text(translator, part, retry_count, cancel_check)
        if not part_ok:
            return source, False
        translated_parts.append(part_final)
        changed = changed or part_final != part

    combined = "".join(translated_parts)
    if not changed:
        return source, True
    if is_usable_translation(source, combined):
        return combined, True
    return source, False


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
        if classify_translation_entry(key, src) != "translate":
            result[key] = src
            if on_pair_done is not None:
                on_pair_done(1)
            continue
        ck = cache_key(src)
        if ck in cache and is_usable_translation(
            src, cache[ck], accept_identical_proper_noun=True
        ):
            result[key] = cache[ck]
            n_cached += 1
            if on_pair_done is not None:
                on_pair_done(1)
            continue
        cache.pop(ck, None)
        final, ok = _translate_segmented_text(translator, src, retry_count, cancel_check)
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
        return read_patchouli_text(page)
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
            return read_patchouli_text(page)
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
    existing_page = read_jar_json_file(target.source_file, target_path) if target.output_mode == "jar_inject" else {}
    page = deepcopy(source_page)
    source_strings = read_patchouli_text(source_page)
    existing_strings = read_patchouli_text(existing_page) if existing_page else {}
    for path_key, existing_value in existing_strings.items():
        source_value = source_strings.get(path_key)
        if source_value is not None and is_usable_translation(source_value, existing_value):
            write_patchouli_text(page, path_key, existing_value)

    existing_strings = read_patchouli_text(page)
    to_translate = diff_keys(source_strings, existing_strings)

    changed = page != existing_page
    failed: dict[str, str] = {}
    n_translated = n_cached = n_fallback = 0

    for path_key in to_translate:
        if cancel_check is not None and cancel_check():
            break
        src = source_strings[path_key]
        ck = cache_key(src)
        if ck in cache and is_usable_translation(
            src, cache[ck], accept_identical_proper_noun=True
        ):
            write_patchouli_text(page, path_key, cache[ck])
            changed = True
            n_cached += 1
            if on_pair_done is not None:
                on_pair_done(1)
            continue
        cache.pop(ck, None)
        final, ok = _translate_patchouli_text(translator, src, retry_count, cancel_check)
        if ok:
            write_patchouli_text(page, path_key, final)
            cache[ck] = final
            changed = True
            n_translated += 1
        else:
            failed[path_key] = src
            n_fallback += 1
        if on_pair_done is not None:
            on_pair_done(1)

    if changed:
        if target.output_mode != "jar_inject":
            raise ValueError("Patchouli resource pack output is no longer supported")
        write_jar_json_file(target.source_file, target_path, page)

    return n_translated, n_cached, n_fallback, failed


def failed_target_name(target: TranslationTarget) -> str:
    location = target.path_in_jar
    if not location and target.target_file:
        location = str(target.target_file)
    if not location:
        location = str(target.source_file)
    return f"{target.mod_id}__{target.format}__{location}"


_FAILED_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _clear_failed_items(output_dir: Path) -> None:
    if not output_dir.is_dir():
        return
    for file_path in output_dir.rglob("*.txt"):
        try:
            file_path.unlink()
        except OSError:
            pass


def _write_failed_items(
    failed_by_target: dict[str, dict[str, str]],
    output_dir: Path,
) -> int:
    """將失敗項目分檔寫入 output_dir。無失敗項目時不建立資料夾，回傳 0。"""
    _clear_failed_items(output_dir)
    total_failed = sum(len(v) for v in failed_by_target.values())
    if total_failed == 0:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for target_name, items in sorted(failed_by_target.items()):
        if not items:
            continue
        category = _failed_item_category(target_name, items)
        safe_name = _FAILED_FILENAME_RE.sub("_", target_name).strip("._")
        if not safe_name:
            safe_name = "failed_items"
        if len(safe_name) > 180:
            digest = hashlib.sha1(target_name.encode("utf-8")).hexdigest()[:12]
            safe_name = f"{safe_name[:167]}_{digest}"
        category_dir = output_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        file_path = category_dir / f"{safe_name}.txt"
        lines = [
            f"失敗項目清單：{target_name}",
            f"分類：{category}",
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


def _failed_item_category(target_name: str, items: dict[str, str]) -> str:
    if "__patchouli_json__" in target_name:
        return "markup_or_book_text"
    classifications = {classify_translation_entry(key, src) for key, src in items.items()}
    if classifications <= {"copy", "skip"}:
        return "copy_or_skip_noise"
    values = list(items.values())
    if all(_looks_failed_fragment(value) for value in values):
        return "short_fragments"
    if any(_looks_markup_heavy(value) for value in values):
        return "markup_or_book_text"
    return "natural_text"


def _looks_failed_fragment(value: str) -> bool:
    text = value.strip()
    if len(text) <= 24:
        return True
    return bool(re.search(r"%\d*\$?[sdifcbxo]|%[sdifcbxo]", text)) and len(text) <= 80


def _looks_markup_heavy(value: str) -> bool:
    return value.count("$(") + value.count("[#](") + value.count("://") >= 2
