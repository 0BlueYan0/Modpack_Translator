"""遠端批次預翻譯：把所有待翻字串去重後批次併發翻進快取，三輪收斂。

僅 backend_mode="remote" 時啟用。在既有逐檔迴圈之前執行：
第 1 輪：收集所有目標檔的待翻字串 → 跨檔去重 → 每請求 N 條、M 條併發批次翻譯；
第 2 輪：整批失敗（>1 條的批）的項目以半批大小重新組批重試；
第 3 輪：仍失敗者（含單條批）沿用主迴圈的逐條分段重試階梯，一條一個任務併發救援。
成功者寫入共用快取，之後逐檔流程幾乎全是快取命中；三輪後仍失敗的極少數字串
自然回退到既有的逐條序列路徑（translate_dict / _process_patchouli）。

執行緒模型：worker threads 只做「請求 + 解析 + 驗證」並回傳結果，
協調者執行緒（呼叫端）收結果、寫快取、發進度——共享可變狀態只有
取消用的 threading.Event，全程無鎖。
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

import httpx
from openai import (
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from modpack_translator.config import ModelConfig
from modpack_translator.pipeline._chat import (
    FATAL_OPENAI_ERRORS,
    TranslatorFatalError,
    describe_openai_error,
    normalize_base_url,
)
from modpack_translator.pipeline.glossary import (
    _BATCH_TERM_CAP,
    Glossary,
    augment_prompt,
)
from modpack_translator.pipeline.postprocessor import process
from modpack_translator.pipeline.preprocessor import (
    diff_keys,
    encode,
    is_usable_translation,
)
from modpack_translator.pipeline.remote_translator import resolve_remote_settings
from modpack_translator.pipeline.runner import (
    _HAS_LETTER_RE,
    _static_translation,
    _translate_patchouli_text,
    _translate_segmented_text,
    cache_key,
    read_existing_target,
    read_target_strings,
)
from modpack_translator.pipeline.scanner import TranslationTarget

# 每批編碼後總字元預算：超過即封批（單條超長自成一批，交給模型一次翻整頁）
_BATCH_CHAR_BUDGET = 4000
# 每累積多少條成功翻譯就呼叫一次 flush_cache
_FLUSH_EVERY = 200
# 待翻條數超過此值且併發 <= 6（舊預設，QSettings 可能殘留）時提示可調高
_CONCURRENCY_HINT_MIN_ITEMS = 500
# 批次模式附加在既有 system prompt 之後的指令
_BATCH_SUFFIX = (
    "\n\n[Batch mode]\n"
    'The user message is a JSON array: [{"id": <int>, "text": "<source>"}, ...].\n'
    "Reply with ONLY a JSON array of the same length using the same ids:\n"
    '[{"id": <same id>, "text": "<translation>"}, ...]\n'
    "Translate each item independently, applying all rules above to each one. "
    "Preserve every {N} placeholder inside each item. "
    "Never merge, omit, or reorder items. No markdown fences, no commentary."
)

_PLACEHOLDER_RE = re.compile(r"\{[0-9]+\}")


@dataclass(frozen=True)
class PrefillItem:
    source: str  # 原始來源字串
    ck: str      # runner.cache_key(source)
    patchouli: bool = False  # 逐條救援時是否走 _translate_patchouli_text 階梯


@dataclass
class PrefillStats:
    total_items: int = 0
    translated: int = 0            # 三輪成功總和
    failed: int = 0                # 三輪後仍失敗（回退逐檔序列路徑）
    batches_sent: int = 0          # 第 1+2 輪送出的批次數
    batches_unparseable: int = 0
    cancelled: bool = False
    round1_failed: int = 0         # 第 1 輪結束時未成功的條數
    round2_items: int = 0          # 進入第 2 輪（縮小批次重試）的條數
    round2_recovered: int = 0
    rescue_items: int = 0          # 進入第 3 輪（逐條救援）的條數
    rescue_recovered: int = 0


@dataclass
class _EncodedItem:
    item: PrefillItem
    encoded: str
    tokens: list[str]


@dataclass
class _BatchResult:
    results: list[tuple[_EncodedItem, str | None]]  # translation 為 None 表示該條失敗
    unparseable: bool = False
    error: str | None = None


def collect_prefill_items(
    targets: list[TranslationTarget],
    lang_code: str,
    cache: dict[str, str],
    glossary: Glossary | None = None,
) -> list[PrefillItem]:
    """收集所有目標檔中「確定會送 API」的待翻字串，以來源雜湊去重。

    排除條件與逐檔序列路徑一致（快取可用、靜態表命中、無字母 fast path），
    保證預翻譯不會翻到序列路徑本來就不送 API 的字串。
    讀檔失敗的目標直接略過，由逐檔階段照常報錯。
    """
    seen: set[str] = set()
    items: list[PrefillItem] = []
    for target in targets:
        try:
            en = read_target_strings(target)
            zh = read_existing_target(target, lang_code)
        except Exception:
            continue
        for key in diff_keys(en, zh):
            src = en[key]
            ck = cache_key(src)
            if ck in seen:
                continue
            if ck in cache and is_usable_translation(src, cache[ck]):
                continue
            static = _static_translation(src)
            if static is not None and is_usable_translation(src, static):
                continue
            if glossary is not None:
                official = glossary.exact_match(src)
                if official is not None and is_usable_translation(src, official):
                    continue  # 逐檔迴圈經 runner 的短路 hook 免 LLM 解決
            encoded, _tokens = encode(src)
            if not _HAS_LETTER_RE.search(_PLACEHOLDER_RE.sub("", encoded)):
                continue
            seen.add(ck)
            items.append(PrefillItem(
                source=src, ck=ck,
                patchouli=target.format == "patchouli_json",
            ))
    return items


def _build_batches(
    items: list[PrefillItem],
    batch_size: int,
    char_budget: int = _BATCH_CHAR_BUDGET,
) -> list[list[_EncodedItem]]:
    batches: list[list[_EncodedItem]] = []
    current: list[_EncodedItem] = []
    current_chars = 0
    for item in items:
        encoded, tokens = encode(item.source)
        if current and (
            len(current) >= batch_size or current_chars + len(encoded) > char_budget
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(_EncodedItem(item=item, encoded=encoded, tokens=tokens))
        current_chars += len(encoded)
    if current:
        batches.append(current)
    return batches


def _batch_max_tokens(batch: list[_EncodedItem]) -> int:
    # 以字元數寬估 token 數，避免批次回應被截斷
    est = 300 + sum(max(80, int(len(e.encoded) * 1.2)) for e in batch)
    return min(8192, est)


def _build_user_message(batch: list[_EncodedItem]) -> str:
    # json.dumps 無損轉義真換行/引號（編碼後字串仍可能含真換行——FTB Quests
    # 的字面 \n 會被 encode 成 {N}，但真換行字元不會）
    return json.dumps(
        [{"id": i, "text": e.encoded} for i, e in enumerate(batch)],
        ensure_ascii=False,
    )


def _parse_batch_response(raw: str, n: int) -> dict[int, str] | None:
    """容錯解析批次回應。回傳 {批內 id: 譯文}（可能不完整）；整體不可解析回 None。"""
    text = raw.strip()
    if not text:
        return None
    text = re.sub(r"^```[A-Za-z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    starts = [i for i in (text.find("["), text.find("{")) if i != -1]
    ends = [i for i in (text.rfind("]"), text.rfind("}")) if i != -1]
    if not starts or not ends or max(ends) <= min(starts):
        return None
    text = text[min(starts) : max(ends) + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 一次修復：移除 ]/} 前的尾逗號
        repaired = re.sub(r",\s*([\]}])", r"\1", text)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            return None
    return _extract_mapping(data, n)


def _extract_mapping(data: object, n: int) -> dict[int, str] | None:
    out: dict[int, str] = {}
    if isinstance(data, dict):
        # 形狀 {"0": "...", "1": "..."}（模型改回 object map）
        for k, v in data.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < n and isinstance(v, str) and idx not in out:
                out[idx] = v
        return out or None
    if isinstance(data, list):
        # 形狀 ["...", "..."]（純字串陣列，長度吻合才敢按位置對應）
        if len(data) == n and all(isinstance(x, str) for x in data):
            return dict(enumerate(data))
        # 正規形狀 [{"id": 0, "text": "..."}]
        for entry in data:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            v = entry.get("text")
            if 0 <= idx < n and isinstance(v, str) and idx not in out:
                out[idx] = v
        return out or None
    return None


def _retry_after_seconds(exc: RateLimitError) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after")
    try:
        return min(120.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def _interruptible_sleep(
    seconds: float,
    cancel_event: threading.Event,
    _sleep: Callable[[float], None],
) -> bool:
    """以 0.25s 切片睡眠，讓取消能即時中斷。被取消回 False。"""
    remaining = seconds
    while remaining > 0:
        if cancel_event.is_set():
            return False
        step = min(0.25, remaining)
        _sleep(step)
        remaining -= step
    return not cancel_event.is_set()


def _stream_with_backoff(
    client: OpenAI,
    cfg: ModelConfig,
    model: str,
    messages: list[dict],
    max_tokens: int,
    cancel_event: threading.Event,
    _sleep: Callable[[float], None],
) -> str | None:
    """送一次串流 chat completion，含逾時/429/5xx 的指數退避重試。

    批次請求與逐條搶救共用。取消或重試耗盡回 None。致命錯誤
    （金鑰/權限/找不到模型/連線失敗）轉拋 TranslatorFatalError 讓整輪中止。
    """
    for attempt in range(cfg.remote_backoff_retries + 1):
        if cancel_event.is_set():
            return None
        retry_after: float | None = None
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=cfg.temperature,
                stream=True,
            )
            chunks: list[str] = []
            for chunk in stream:
                if cancel_event.is_set():
                    return None
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    chunks.append(delta.content)
            return "".join(chunks).strip()
        except (APITimeoutError, httpx.TimeoutException):
            # 逾時可重試。APITimeoutError 是 APIConnectionError 的子類，
            # 必須在 FATAL_OPENAI_ERRORS 之前攔截，否則一次慢請求就中止整輪。
            pass
        except RateLimitError as exc:
            retry_after = _retry_after_seconds(exc)
        except FATAL_OPENAI_ERRORS as exc:
            raise TranslatorFatalError(describe_openai_error(exc)) from exc
        except APIStatusError as exc:
            if exc.status_code < 500:
                return None  # 其他 4xx：重試無望，放棄該批（交給逐條序列路徑）
            # 5xx 視為暫時性，退避重試
        if attempt >= cfg.remote_backoff_retries:
            return None
        delay = (
            retry_after
            if retry_after is not None
            else min(60.0, 2.0 * (2**attempt)) * random.uniform(0.5, 1.5)
        )
        if not _interruptible_sleep(delay, cancel_event, _sleep):
            return None
    return None


def _request_batch_raw(
    client: OpenAI,
    cfg: ModelConfig,
    model: str,
    system_prompt: str,
    batch: list[_EncodedItem],
    max_tokens: int,
    cancel_event: threading.Event,
    _sleep: Callable[[float], None],
    glossary_block: str = "",
) -> str | None:
    """組批次 messages 後委派給 _stream_with_backoff。回傳語意同該函式。"""
    messages = [
        {"role": "system", "content": system_prompt + _BATCH_SUFFIX + glossary_block},
        {"role": "user", "content": _build_user_message(batch)},
    ]
    return _stream_with_backoff(client, cfg, model, messages, max_tokens, cancel_event, _sleep)


def _process_batch(
    client: OpenAI,
    cfg: ModelConfig,
    model: str,
    system_prompt: str,
    batch: list[_EncodedItem],
    cancel_event: threading.Event,
    _sleep: Callable[[float], None],
    glossary: Glossary | None = None,
) -> _BatchResult:
    """worker thread 進入點：請求 → 解析（不可解析時重送一次）→ 逐條驗證。"""
    try:
        max_tokens = _batch_max_tokens(batch)
        glossary_block = "" if glossary is None else glossary.format_block(
            glossary.match_terms(enc.encoded for enc in batch), cap=_BATCH_TERM_CAP
        )
        mapping: dict[int, str] = {}
        unparseable = False
        for _parse_attempt in (0, 1):
            raw = _request_batch_raw(
                client, cfg, model, system_prompt, batch, max_tokens, cancel_event, _sleep,
                glossary_block,
            )
            if raw is None:
                break  # 取消或請求層重試耗盡：不再做解析重試
            parsed = _parse_batch_response(raw, len(batch))
            if parsed is not None:
                mapping = parsed
                unparseable = False
                break
            unparseable = True
        results: list[tuple[_EncodedItem, str | None]] = []
        for i, enc in enumerate(batch):
            final: str | None = None
            raw_text = mapping.get(i)
            if raw_text is not None:
                # 與序列路徑完全相同的逐條驗證：硬性 token 保留 + 可用性檢查
                candidate, ok = process(raw_text, enc.encoded, enc.tokens)
                if ok and is_usable_translation(enc.item.source, candidate):
                    final = candidate
            results.append((enc, final))
        return _BatchResult(results=results, unparseable=unparseable)
    except TranslatorFatalError:
        raise
    except Exception as exc:  # noqa: BLE001 — 單批意外失敗不可拖垮整輪
        return _BatchResult(
            results=[(enc, None) for enc in batch],
            error=f"{type(exc).__name__}: {exc}",
        )


class _RescueTranslator:
    """逐條救援用 translator：請求形狀與 RemoteTranslator.translate 相同
    （純 system prompt、無批次後綴、max_tokens=cfg.max_tokens），
    但套用批次層的退避重試。無可變狀態，多執行緒可共用。
    請求耗盡/4xx/取消時回空字串，讓後處理驗證安全判失敗。"""

    def __init__(
        self,
        client: OpenAI,
        cfg: ModelConfig,
        model: str,
        system_prompt: str,
        cancel_event: threading.Event,
        _sleep: Callable[[float], None],
        glossary: Glossary | None = None,
    ) -> None:
        self._client = client
        self._cfg = cfg
        self._model = model
        self._system_prompt = system_prompt
        self._cancel_event = cancel_event
        self._sleep = _sleep
        self.glossary = glossary  # public：runner 的短路 hook 以 getattr 取用

    def translate(self, text: str, cancel_check: Callable[[], bool] | None = None) -> str:
        raw = _stream_with_backoff(
            self._client,
            self._cfg,
            self._model,
            [
                {"role": "system", "content": augment_prompt(self._system_prompt, self.glossary, [text])},
                {"role": "user", "content": text},
            ],
            self._cfg.max_tokens,
            self._cancel_event,
            self._sleep,
        )
        return raw or ""


def _rescue_item(
    translator: _RescueTranslator,
    item: PrefillItem,
    retry_count: int,
    cancel_event: threading.Event,
) -> tuple[PrefillItem, str | None]:
    """worker thread 進入點：以主迴圈的分段重試階梯翻譯單條字串。"""
    try:
        fn = _translate_patchouli_text if item.patchouli else _translate_segmented_text
        final, ok = fn(translator, item.source, retry_count, cancel_check=cancel_event.is_set)
        return item, (final if ok else None)
    except TranslatorFatalError:
        raise
    except Exception:  # noqa: BLE001 — 單條意外失敗不可拖垮整輪
        return item, None


@dataclass
class _RunContext:
    """協調者狀態。除 cancel_event 外只有協調者執行緒讀寫，維持無鎖模型。"""

    client: OpenAI
    cfg: ModelConfig
    model: str
    system_prompt: str
    cache: dict[str, str]
    stats: PrefillStats
    total: int
    cancel_event: threading.Event
    cancel_check: Callable[[], bool] | None
    on_progress: Callable[[int, int], None] | None
    on_log: Callable[[str], None] | None
    flush_cache: Callable[[], None] | None
    sleep: Callable[[float], None]
    glossary: Glossary | None = None
    resolved: int = 0
    since_flush: int = 0
    errors_logged: int = 0


def _check_cancelled(ctx: _RunContext) -> bool:
    if ctx.cancel_check is not None and ctx.cancel_check():
        ctx.cancel_event.set()
        ctx.stats.cancelled = True
        return True
    return False


def _settle(ctx: _RunContext, item: PrefillItem, final: str | None) -> None:
    """一條字串最終落定：成功寫快取、失敗計數，並依累積量 flush。"""
    ctx.resolved += 1
    if final is not None:
        ctx.cache[item.ck] = final
        ctx.stats.translated += 1
        ctx.since_flush += 1
    else:
        ctx.stats.failed += 1
    if ctx.flush_cache is not None and ctx.since_flush >= _FLUSH_EVERY:
        ctx.flush_cache()
        ctx.since_flush = 0


def _emit_progress(ctx: _RunContext) -> None:
    if ctx.on_progress is not None:
        ctx.on_progress(ctx.resolved, ctx.total)


def _run_batches(
    pool: ThreadPoolExecutor,
    ctx: _RunContext,
    batches: list[list[_EncodedItem]],
) -> tuple[list[PrefillItem], list[PrefillItem]]:
    """跑一輪批次請求，回傳 (多條批的失敗項, 單條批的失敗項)。

    單條批重送同樣的請求幾乎必然重蹈覆轍，分流出來直接進逐條救援。
    取消時提前返回（未收割的項目不落定、不計數）。
    """
    futures = [
        pool.submit(
            _process_batch, ctx.client, ctx.cfg, ctx.model, ctx.system_prompt,
            batch, ctx.cancel_event, ctx.sleep, ctx.glossary,
        )
        for batch in batches
    ]
    failed_multi: list[PrefillItem] = []
    failed_single: list[PrefillItem] = []
    for fut in as_completed(futures):
        if _check_cancelled(ctx):
            break
        try:
            result = fut.result()
        except TranslatorFatalError:
            ctx.cancel_event.set()
            raise
        ctx.stats.batches_sent += 1
        if result.unparseable:
            ctx.stats.batches_unparseable += 1
        if result.error is not None and ctx.on_log is not None and ctx.errors_logged < 5:
            ctx.on_log(f"[警告] 一批預翻譯失敗（將進入下一輪重試）：{result.error}")
            ctx.errors_logged += 1
        sink = failed_single if len(result.results) == 1 else failed_multi
        for enc, final in result.results:
            if final is not None:
                _settle(ctx, enc.item, final)
            else:
                sink.append(enc.item)
        _emit_progress(ctx)
    return failed_multi, failed_single


def _run_rescue_round(
    pool: ThreadPoolExecutor,
    ctx: _RunContext,
    items: list[PrefillItem],
    retry_count: int,
) -> None:
    """第 3 輪：逐條併發救援，結果直接最終落定（成功寫快取／失敗計 failed）。"""
    translator = _RescueTranslator(
        ctx.client, ctx.cfg, ctx.model, ctx.system_prompt, ctx.cancel_event, ctx.sleep,
        ctx.glossary,
    )
    futures = [
        pool.submit(_rescue_item, translator, item, retry_count, ctx.cancel_event)
        for item in items
    ]
    for fut in as_completed(futures):
        if _check_cancelled(ctx):
            break
        try:
            item, final = fut.result()
        except TranslatorFatalError:
            ctx.cancel_event.set()
            raise
        if final is not None:
            ctx.stats.rescue_recovered += 1
        _settle(ctx, item, final)
        _emit_progress(ctx)


def run_prefill(
    items: list[PrefillItem],
    cfg: ModelConfig,
    system_prompt: str,
    cache: dict[str, str],
    *,
    retry_count: int = 0,
    cancel_check: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,  # (done, total)
    on_log: Callable[[str], None] | None = None,
    flush_cache: Callable[[], None] | None = None,
    glossary: Glossary | None = None,
    _sleep: Callable[[float], None] = time.sleep,
) -> PrefillStats:
    """三輪收斂翻譯 items：批次 → 縮小批次重試 → 併發逐條救援。

    成功者寫入 cache（僅協調者執行緒寫入）。retry_count 傳給第 3 輪的
    分段重試階梯，與主迴圈語意一致。
    """
    stats = PrefillStats(total_items=len(items))
    if not items:
        return stats

    base_url, api_key, model = resolve_remote_settings(cfg)
    client = OpenAI(
        base_url=f"{normalize_base_url(base_url)}/v1",
        api_key=api_key or "not-needed",
        timeout=cfg.remote_timeout_s,
        max_retries=0,  # 退避重試自己管理（_stream_with_backoff）
    )
    batches = _build_batches(items, cfg.remote_batch_size)
    if on_log is not None:
        on_log(
            f"批次預翻譯：{len(items)} 條待翻字串（去重後），"
            f"分 {len(batches)} 批、併發 {cfg.remote_concurrency}。"
        )
        if cfg.remote_concurrency <= 6 and len(items) > _CONCURRENCY_HINT_MIN_ITEMS:
            on_log(
                f"提示：目前併發僅 {cfg.remote_concurrency}，付費 API 可在設定中"
                "調高併發請求數以加速（429 會自動退避，安全）。"
            )
    if on_progress is not None:
        on_progress(0, len(items))

    ctx = _RunContext(
        client=client, cfg=cfg, model=model, system_prompt=system_prompt,
        cache=cache, stats=stats, total=len(items),
        cancel_event=threading.Event(), cancel_check=cancel_check,
        on_progress=on_progress, on_log=on_log, flush_cache=flush_cache,
        sleep=_sleep, glossary=glossary,
    )
    pool = ThreadPoolExecutor(max_workers=cfg.remote_concurrency)
    try:
        failed_multi, failed_single = _run_batches(pool, ctx, batches)
        stats.round1_failed = len(failed_multi) + len(failed_single)
        if not stats.cancelled and stats.round1_failed and on_log is not None:
            on_log(
                f"第 1 輪剩 {stats.round1_failed} 條未成功："
                f"{len(failed_multi)} 條縮小批次重試、{len(failed_single)} 條待逐條救援。"
            )

        if not stats.cancelled and failed_multi:
            half = max(1, cfg.remote_batch_size // 2)
            stats.round2_items = len(failed_multi)
            if on_log is not None:
                on_log(f"第 2 輪：以每批 {half} 條重試 {len(failed_multi)} 條…")
            before = stats.translated
            f2_multi, f2_single = _run_batches(
                pool, ctx, _build_batches(failed_multi, half)
            )
            stats.round2_recovered = stats.translated - before
            failed_single += f2_multi + f2_single
            if not stats.cancelled and on_log is not None:
                on_log(f"第 2 輪完成：救回 {stats.round2_recovered} 條。")

        if not stats.cancelled and failed_single:
            stats.rescue_items = len(failed_single)
            if on_log is not None:
                on_log(
                    f"第 3 輪：逐條並行救援 {stats.rescue_items} 條"
                    f"（沿用分段重試階梯、併發 {cfg.remote_concurrency}）…"
                )
            _run_rescue_round(pool, ctx, failed_single, retry_count)
            if not stats.cancelled and on_log is not None:
                on_log(f"第 3 輪完成：救回 {stats.rescue_recovered} 條。")
    finally:
        # 取消/致命錯誤：丟棄佇列中的任務；在途請求由 cancel_event 在下個 chunk 中止
        pool.shutdown(wait=False, cancel_futures=True)
    return stats


def prefill_translation_cache(
    targets: list[TranslationTarget],
    cfg: ModelConfig,
    system_prompt: str,
    lang_code: str,
    cache: dict[str, str],
    *,
    retry_count: int = 0,
    cancel_check: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    flush_cache: Callable[[], None] | None = None,
    glossary: Glossary | None = None,
    _sleep: Callable[[float], None] = time.sleep,
) -> PrefillStats:
    """GUI 與 CLI 共用的入口：remote 模式且開關開啟才收集並執行預翻譯。"""
    if cfg.backend_mode != "remote" or not cfg.remote_prefill:
        return PrefillStats()
    items = collect_prefill_items(targets, lang_code, cache, glossary)
    if not items:
        if on_log is not None:
            on_log("批次預翻譯：沒有需要預翻譯的字串（快取已涵蓋）。")
        return PrefillStats()
    start = time.monotonic()
    stats = run_prefill(
        items,
        cfg,
        system_prompt,
        cache,
        retry_count=retry_count,
        cancel_check=cancel_check,
        on_progress=on_progress,
        on_log=on_log,
        flush_cache=flush_cache,
        glossary=glossary,
        _sleep=_sleep,
    )
    elapsed = time.monotonic() - start
    if on_log is not None:
        if stats.cancelled:
            on_log(f"批次預翻譯已取消（已完成 {stats.translated} 條）。")
        else:
            rate = (
                f"（{stats.translated / elapsed:.1f} 條/秒）"
                if elapsed > 0 and stats.translated else ""
            )
            on_log(
                f"批次預翻譯完成：成功 {stats.translated} 條、"
                f"待逐條重試 {stats.failed} 條，耗時 {elapsed:.1f} 秒{rate}。"
                "開始逐檔寫入…"
            )
    return stats
