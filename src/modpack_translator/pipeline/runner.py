from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from modpack_translator.pipeline import mdx
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
    write_jar_text,
)
from modpack_translator.pipeline.postprocessor import process
from modpack_translator.pipeline.preprocessor import (
    _preserves_required_tokens,
    classify_translation_entry,
    decode,
    diff_keys,
    encode,
    jar_member_exists,
    read_inline_snbt_text,
    is_usable_translation,
    read_jar_text,
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


def _enforce_glossary(glossary: Any, source: str, translated: str) -> str:
    """對已通過驗證的譯文套用用語庫事後保證。

    只在替換後仍保留全部硬性 token 時採用（fail-safe：替換到還原後
    token 內容的極端情況退回原譯文，交由既有驗證處理）。
    """
    if glossary is None:
        return translated
    enforced = glossary.enforce(translated)
    if enforced == translated or not _preserves_required_tokens(source, enforced):
        return translated
    return enforced


def normalize_cache_with_glossary(cache: dict[str, str], glossary: Any) -> int:
    """快取正規化：快取 key 是 sha256(原文) 不存原文，但用語庫的詞我們知道
    原文——對每個詞算 cache_key 精準定位槽位，存在且不等於譯名就覆寫。
    每輪執行時呼叫、冪等、零 API 成本；只動既存槽位，不注入新條目。
    回傳覆寫條數。"""
    if glossary is None:
        return 0
    changed = 0
    for en, zh in glossary.terms.items():
        ck = cache_key(en)
        if ck in cache and cache[ck] != zh:
            cache[ck] = zh
            changed += 1
    return changed


def iter_all_source_strings(modpack_path, lang_code: str = "zh_tw"):
    """走訪模組包所有來源字串（含已翻譯檔，停用待翻過濾）。
    供 sync_source_sidecar 反查 hash→英文;不用於翻譯流程。"""
    from modpack_translator.pipeline.scanner import ModpackScanner

    scanner = ModpackScanner()
    for target in scanner.scan(modpack_path, lang_code, include_translated=True):
        try:
            strings = read_target_strings(target)
        except Exception:
            continue
        yield from strings.values()


def sync_source_sidecar(cache: dict[str, str], source_strings, sidecar_path) -> int:
    """建立/更新與 cache 同 key 集合的 hash→英文 對照檔（sidecar）。

    cache 的 key 是 sha256(原文)、不存原文;此檔把原文補回來,供人工稽核
    「某個 hash 原本英文是什麼」。輸出的 key 集合與 cache 完全一致(無法從
    本包來源反查者以既有 sidecar 值補、再退為空字串),故稱「與快取同步」。
    回傳成功反查(非空)的條數。"""
    src_by_hash: dict[str, str] = {}
    for s in source_strings:
        if isinstance(s, str) and s:
            src_by_hash.setdefault(cache_key(s), s)
    p = Path(sidecar_path)
    prior: dict[str, str] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prior = loaded
        except (OSError, ValueError):
            prior = {}
    out = {h: (src_by_hash.get(h) or prior.get(h, "")) for h in cache}
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return sum(1 for v in out.values() if v)


def source_sidecar_path(cache_path) -> Path:
    """cache 旁的 hash→英文 sidecar 路徑(translation_sources.json)。"""
    return Path(cache_path).with_name("translation_sources.json")


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
    # 模型輸出關卡開啟專有名詞豁免：模型對模組名、人名等原樣返回是正確判斷；
    # 但整串命中用語庫的原樣返回不放行（守門），改以官方譯名取代。
    # 已放行的輸出再套 enforce，替換句中殘留的英文詞彙。
    if ok and is_usable_translation(
        source, final, accept_identical_proper_noun=True, glossary=glossary
    ):
        return _enforce_glossary(glossary, source, final), True
    # 整串命中用語庫者已在呼叫模型前由上方 exact_match 短路，不會走到這裡；
    # 故無需在模型輸出後重複 exact_match 回退。
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
    glossary = getattr(translator, "glossary", None)
    pack_context = getattr(translator, "pack_context", None)
    to_translate = diff_keys(en_dict, zh_existing, glossary=glossary)
    result: dict[str, str] = {}
    failed: dict[str, str] = {}
    n_translated = n_cached = n_fallback = 0

    # 既有譯文遷移：與快取值一致代表是本工具翻的（非人工修改），
    # 套 enforce 修復句中殘留的英文詞彙並寫回；不一致者一律不動。
    # 不計入統計——是零成本的順帶修復，非本輪翻譯量。
    if glossary is not None:
        for key, existing_value in zh_existing.items():
            if key in to_translate:
                continue
            src = en_dict.get(key)
            if src is None:
                continue
            ck = cache_key(src)
            if cache.get(ck) != existing_value:
                continue
            enforced = _enforce_glossary(glossary, src, existing_value)
            if enforced != existing_value:
                result[key] = enforced
                cache[ck] = enforced

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
            src, cache[ck], accept_identical_proper_noun=True, glossary=glossary
        ):
            value = _enforce_glossary(glossary, src, cache[ck])
            if value != cache[ck]:
                cache[ck] = value
            result[key] = value
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
            if pack_context is not None:
                pack_context.maybe_record(src, final, glossary)
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
    elif target.format == "oracle_mdx":
        return mdx.extract_mdx(read_jar_text(target.source_file, target.path_in_jar))
    return {}


def read_existing_target(target: TranslationTarget, lang_code: str) -> dict[str, str]:
    """讀既有譯文供 diff/重用。既有檔可能在非正規(大寫)路徑,故一律讀
    existing_* 而非寫入用的 target_*(正規小寫,可能尚不存在)。"""
    if target.output_mode == "jar_inject":
        existing_path = target.existing_path_in_jar
        if not existing_path:
            return {}
        if target.format == "json_lang":
            return read_jar_json_lang(target.source_file, existing_path)
        if target.format == "legacy_lang":
            return read_jar_legacy_lang(target.source_file, existing_path)
        if target.format == "patchouli_json":
            page = read_jar_json_file(target.source_file, existing_path)
            return read_patchouli_text(page)
        if target.format == "oracle_mdx":
            if not existing_path:
                return {}
            try:
                return mdx.extract_mdx(read_jar_text(target.source_file, existing_path))
            except (KeyError, OSError):
                return {}
        return {}

    existing_file = target.existing_file
    if target.format in ("ftbq_snbt", "heracles_snbt"):
        return read_existing_snbt(existing_file) if existing_file else {}
    elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
        return {}
    elif target.format == "bq_lang":
        return read_existing_bq_lang(existing_file) if existing_file else {}
    else:
        if existing_file and existing_file.exists():
            return json.loads(existing_file.read_text(encoding="utf-8"))
    return {}


def _output_exists(target: TranslationTarget) -> bool:
    """正規小寫寫入目標是否已存在。"""
    if target.output_mode == "jar_inject":
        return bool(target.target_path_in_jar) and jar_member_exists(
            target.source_file, target.target_path_in_jar
        )
    if target.target_file is not None:
        return target.target_file.exists()
    return True


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

    if target.format == "oracle_mdx":
        return _process_oracle_mdx(target, translator, cache, retry_count, cancel_check, on_pair_done)

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

    # 寫入 payload 以既有譯文打底：遷移到正規小寫檔時所有既有 key 一併帶過去
    # （只寫 result 會讓新小寫檔僅含本輪 diff,其餘 fallback 成英文）。
    # inline 格式就地改寫來源檔,zh_existing 為空,payload 即 result。
    write_payload = {**zh_existing, **result}
    # result 為空但既有譯文在非正規(大寫)檔、正規小寫檔尚不存在 → 純遷移,仍要建立小寫檔。
    should_write = bool(result) or (bool(zh_existing) and not _output_exists(target))

    if should_write:
        if target.output_mode == "jar_inject":
            if not target.target_path_in_jar:
                raise ValueError(f"Missing jar target path for {target.source_file}")
            if target.format == "json_lang":
                write_jar_json_lang(target.source_file, target.target_path_in_jar, write_payload)
            elif target.format == "legacy_lang":
                write_jar_legacy_lang(target.source_file, target.target_path_in_jar, write_payload)
            else:
                raise ValueError(f"Unsupported jar injection format: {target.format}")
        elif target.format in ("ftbq_snbt", "heracles_snbt"):
            write_inplace_snbt(target.source_file, lang_code, write_payload, target.target_file)
        elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
            write_inline_snbt(target.source_file, result)
        elif target.format == "bq_lang":
            write_inplace_bq_lang(target.source_file, lang_code, write_payload, target.target_file)
        else:
            write_inplace_json(target.source_file, lang_code, write_payload, target.target_file)

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

    glossary = getattr(translator, "glossary", None)
    pack_context = getattr(translator, "pack_context", None)
    source_page = read_patchouli_page(target.source_file, target.path_in_jar)
    target_path = target.target_path_in_jar or target.path_in_jar
    if not target_path:
        raise ValueError(f"Missing Patchouli target path for {target.source_file}")
    # 既有譯頁可能在大寫語系目錄;讀 existing_path 供重用/diff,寫入一律正規小寫 target_path。
    existing_page = (
        read_jar_json_file(target.source_file, target.existing_path_in_jar)
        if target.output_mode == "jar_inject"
        else {}
    )
    page = deepcopy(source_page)
    source_strings = read_patchouli_text(source_page)
    existing_strings = read_patchouli_text(existing_page) if existing_page else {}
    for path_key, existing_value in existing_strings.items():
        source_value = source_strings.get(path_key)
        if source_value is None:
            continue
        if not is_usable_translation(source_value, existing_value, glossary=glossary):
            continue
        ck = cache_key(source_value)
        if cache.get(ck) == existing_value:
            enforced = _enforce_glossary(glossary, source_value, existing_value)
            if enforced != existing_value:
                cache[ck] = enforced
                existing_value = enforced
        write_patchouli_text(page, path_key, existing_value)

    existing_strings = read_patchouli_text(page)
    to_translate = diff_keys(source_strings, existing_strings, glossary=glossary)

    changed = page != existing_page
    failed: dict[str, str] = {}
    n_translated = n_cached = n_fallback = 0

    for path_key in to_translate:
        if cancel_check is not None and cancel_check():
            break
        src = source_strings[path_key]
        ck = cache_key(src)
        if ck in cache and is_usable_translation(
            src, cache[ck], accept_identical_proper_noun=True, glossary=glossary
        ):
            value = _enforce_glossary(glossary, src, cache[ck])
            if value != cache[ck]:
                cache[ck] = value
            write_patchouli_text(page, path_key, value)
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
            if pack_context is not None:
                pack_context.maybe_record(src, final, glossary)
        else:
            failed[path_key] = src
            n_fallback += 1
        if on_pair_done is not None:
            on_pair_done(1)

    # 純遷移:既有譯頁在大寫目錄、正規小寫目錄尚不存在時,即使 page 未變也要建立小寫檔。
    target_missing = target.output_mode == "jar_inject" and not jar_member_exists(target.source_file, target_path)
    if changed or (target_missing and bool(existing_page)):
        if target.output_mode != "jar_inject":
            raise ValueError("Patchouli resource pack output is no longer supported")
        write_jar_json_file(target.source_file, target_path, page)

    return n_translated, n_cached, n_fallback, failed


def _process_oracle_mdx(
    target: TranslationTarget,
    translator: Any,
    cache: dict[str, str],
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
) -> tuple[int, int, int, dict[str, str]]:
    raw = read_jar_text(target.source_file, target.path_in_jar)
    en = mdx.extract_mdx(raw)
    if not en:
        return 0, 0, 0, {}
    zh_existing = read_existing_target(target, target.output_lang_code)
    result, n_translated, n_cached, n_fallback, failed = translate_dict(
        en, zh_existing, translator, cache, retry_count, cancel_check, on_pair_done
    )
    merged = {**zh_existing, **result}
    should_write = bool(result) or (
        bool(zh_existing) and not jar_member_exists(target.source_file, target.target_path_in_jar)
    )
    if should_write:
        new_raw = mdx.rebuild_mdx(raw, merged)
        # 內容未變則不重寫(避免 re-run 無謂改動 jar)
        if not (jar_member_exists(target.source_file, target.target_path_in_jar)
                and read_jar_text(target.source_file, target.target_path_in_jar) == new_raw):
            write_jar_text(target.source_file, target.target_path_in_jar, new_raw)
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
