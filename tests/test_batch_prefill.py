import json
import threading
import zipfile

import httpx
import pytest
from openai import (
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

import modpack_translator.pipeline.batch_prefill as bp
from modpack_translator.config import ModelConfig
from modpack_translator.pipeline._chat import TranslatorFatalError
from modpack_translator.pipeline.runner import cache_key, translate_dict
from modpack_translator.pipeline.scanner import TranslationTarget


# ---------------------------------------------------------------- fakes

class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _HandlerCompletions:
    """handler(kwargs, call_index) 回傳完整回應文字，或回傳 Exception 使其被 raise。"""

    def __init__(self, handler, calls):
        self._handler = handler
        self._calls = calls
        self._lock = threading.Lock()

    def create(self, **kwargs):
        with self._lock:
            self._calls.append(kwargs)
            idx = len(self._calls) - 1
        out = self._handler(kwargs, idx)
        if isinstance(out, Exception):
            raise out
        return iter([_Chunk(out)])


class _Chat:
    def __init__(self, handler, calls):
        self.completions = _HandlerCompletions(handler, calls)


class FakeClient:
    def __init__(self, handler, calls):
        self.chat = _Chat(handler, calls)


def _echo_handler(kwargs, idx):
    """把每條 text 前綴「譯」回傳合法 JSON 陣列（通過 CJK 與 token 驗證）。"""
    payload = json.loads(kwargs["messages"][1]["content"])
    return json.dumps(
        [{"id": e["id"], "text": "譯" + e["text"]} for e in payload],
        ensure_ascii=False,
    )


def _script_handler(script):
    """依呼叫次序回傳 script 內容（超出範圍時重複最後一項）。"""

    def handler(kwargs, idx):
        return script[min(idx, len(script) - 1)]

    return handler


def _remote_cfg(**overrides) -> ModelConfig:
    base = dict(
        backend_mode="remote",
        remote_base_url="http://x/v1",
        remote_api_key="sk-test",
        remote_model="test-model",
    )
    base.update(overrides)
    return ModelConfig(**base)


def _mk_items(n):
    sources = [f"Collect some shiny stones number variant {i}" for i in range(n)]
    return [bp.PrefillItem(source=s, ck=cache_key(s)) for s in sources]


def _rate_limit_error(headers=None):
    req = httpx.Request("POST", "http://x")
    resp = httpx.Response(429, request=req, headers=headers or {})
    return RateLimitError("rate limited", response=resp, body=None)


def _patch_client(monkeypatch, handler):
    calls: list[dict] = []
    monkeypatch.setattr(bp, "OpenAI", lambda **kw: FakeClient(handler, calls))
    return calls


# ---------------------------------------------------------------- 解析器

def test_parse_canonical_array():
    raw = '[{"id": 0, "text": "甲"}, {"id": 1, "text": "乙"}]'
    assert bp._parse_batch_response(raw, 2) == {0: "甲", 1: "乙"}


def test_parse_strips_markdown_fences_and_chatter():
    raw = '以下是翻譯：\n```json\n[{"id": 0, "text": "甲"}]\n```\n希望有幫助！'
    assert bp._parse_batch_response(raw, 1) == {0: "甲"}


def test_parse_object_map_shape():
    raw = '{"0": "甲", "1": "乙"}'
    assert bp._parse_batch_response(raw, 2) == {0: "甲", 1: "乙"}


def test_parse_plain_string_array_only_when_length_matches():
    assert bp._parse_batch_response('["甲", "乙"]', 2) == {0: "甲", 1: "乙"}
    assert bp._parse_batch_response('["甲", "乙"]', 3) is None


def test_parse_repairs_trailing_comma():
    raw = '[{"id": 0, "text": "甲"},]'
    assert bp._parse_batch_response(raw, 1) == {0: "甲"}


def test_parse_partial_and_dirty_entries():
    raw = (
        '[{"id": 0, "text": "甲"}, {"id": "1", "text": "乙"},'
        ' {"id": 9, "text": "越界"}, {"id": 0, "text": "重複"},'
        ' {"id": 2, "text": 123}, "不是物件"]'
    )
    # id 可為字串數字；越界/非字串值/重複 id（取首見）都被清掉
    assert bp._parse_batch_response(raw, 3) == {0: "甲", 1: "乙"}


def test_parse_garbage_returns_none():
    assert bp._parse_batch_response("完全不是 JSON", 2) is None
    assert bp._parse_batch_response("", 2) is None
    assert bp._parse_batch_response("[{broken", 2) is None


def test_parse_translation_may_contain_braces_and_newlines():
    raw = json.dumps([{"id": 0, "text": "第一行\n{0} 第二行"}], ensure_ascii=False)
    assert bp._parse_batch_response(raw, 1) == {0: "第一行\n{0} 第二行"}


# ---------------------------------------------------------------- 組批

def test_build_batches_respects_batch_size():
    batches = bp._build_batches(_mk_items(25), batch_size=10)
    assert [len(b) for b in batches] == [10, 10, 5]


def test_build_batches_respects_char_budget():
    items = [
        bp.PrefillItem(source="a" * 60, ck="ck1"),
        bp.PrefillItem(source="b" * 60, ck="ck2"),
        bp.PrefillItem(source="c" * 60, ck="ck3"),
    ]
    batches = bp._build_batches(items, batch_size=10, char_budget=100)
    assert [len(b) for b in batches] == [1, 1, 1]


def test_build_batches_oversized_item_is_singleton():
    items = [
        bp.PrefillItem(source="short one", ck="ck1"),
        bp.PrefillItem(source="x" * 500, ck="ck2"),
        bp.PrefillItem(source="short two", ck="ck3"),
    ]
    batches = bp._build_batches(items, batch_size=10, char_budget=100)
    assert [[e.item.ck for e in b] for b in batches] == [["ck1"], ["ck2"], ["ck3"]]


def test_build_batches_size_one_degenerates():
    batches = bp._build_batches(_mk_items(3), batch_size=1)
    assert [len(b) for b in batches] == [1, 1, 1]


# ---------------------------------------------------------------- 收集

def _kubejs_target(tmp_path, en: dict, name="en_us.json") -> TranslationTarget:
    en_path = tmp_path / name
    en_path.write_text(json.dumps(en, ensure_ascii=False), encoding="utf-8")
    return TranslationTarget(
        source_file=en_path,
        path_in_jar=None,
        mod_id="kubejs",
        format="kubejs_json",
        output_mode="in_place",
        target_file=tmp_path / "zh_tw.json",
    )


def test_collect_dedups_and_applies_serial_path_exclusions(tmp_path):
    dup = "Collect ten pieces of raw iron ore for the blacksmith"
    cached = "Cached forever gemstones inside the vault"
    target = _kubejs_target(tmp_path, {
        "quest.a.desc": dup,
        "quest.b.desc": dup,                    # 同來源去重
        "quest.c.desc": "Cat",                  # 靜態表命中，不送 API
        "quest.d.desc": cached,                 # 快取已有可用譯文
        "quest.e.desc": "[%s]",                 # 無字母 fast path
        "quest.f.desc": "Bring the ancient sword to the village elder",
    })
    cache = {cache_key(cached): "保險庫裡的永恆寶石"}
    items = bp.collect_prefill_items([target], "zh_tw", cache)
    assert sorted(i.source for i in items) == sorted([
        dup,
        "Bring the ancient sword to the village elder",
    ])


def test_collect_skips_unreadable_target(tmp_path):
    broken = TranslationTarget(
        source_file=tmp_path / "missing.json",
        path_in_jar=None,
        mod_id="broken",
        format="kubejs_json",
        output_mode="in_place",
    )
    ok = _kubejs_target(tmp_path, {"quest.a.desc": "Feed the hungry wolves with fresh meat"})
    items = bp.collect_prefill_items([broken, ok], "zh_tw", {})
    assert [i.source for i in items] == ["Feed the hungry wolves with fresh meat"]


def test_collect_includes_patchouli_without_classify(tmp_path):
    jar_path = tmp_path / "mod.jar"
    page = {
        "name": "Machine basics",
        "pages": [{"type": "text", "text": "Long guide text about the crushing machine"}],
    }
    entry_path = "assets/mod/patchouli_books/book/en_us/entries/basics.json"
    with zipfile.ZipFile(jar_path, "w") as zf:
        zf.writestr(entry_path, json.dumps(page))
    target = TranslationTarget(
        source_file=jar_path,
        path_in_jar=entry_path,
        mod_id="mod",
        format="patchouli_json",
        output_mode="jar_inject",
        target_path_in_jar=entry_path.replace("en_us", "zh_tw"),
    )
    items = bp.collect_prefill_items([target], "zh_tw", {})
    assert "Long guide text about the crushing machine" in [i.source for i in items]


# ---------------------------------------------------------------- run_prefill

def test_run_prefill_happy_path(monkeypatch):
    calls = _patch_client(monkeypatch, _echo_handler)
    items = _mk_items(5)
    cfg = _remote_cfg(remote_batch_size=2, remote_concurrency=2)
    cache: dict[str, str] = {}
    progress: list[tuple[int, int]] = []

    stats = bp.run_prefill(
        items, cfg, "系統提示", cache,
        on_progress=lambda d, t: progress.append((d, t)),
    )

    assert stats.translated == 5 and stats.failed == 0
    assert stats.batches_sent == 3 and stats.batches_unparseable == 0
    for item in items:
        assert cache[item.ck] == "譯" + item.source
    # 進度單調遞增且最終到達 (5, 5)
    assert progress[0] == (0, 5)
    assert [d for d, _ in progress] == sorted(d for d, _ in progress)
    assert progress[-1] == (5, 5)
    # 請求形狀：串流、批次後綴、user 訊息為可往返的 JSON 陣列
    kw = calls[0]
    assert kw["stream"] is True
    assert kw["model"] == "test-model"
    assert kw["messages"][0]["content"].startswith("系統提示")
    assert "[Batch mode]" in kw["messages"][0]["content"]
    payload = json.loads(kw["messages"][1]["content"])
    assert all(set(e) == {"id", "text"} for e in payload)
    assert kw["max_tokens"] > 0
    assert kw["temperature"] == cfg.temperature


def test_run_prefill_sets_client_timeout_and_no_sdk_retries(monkeypatch):
    captured: dict = {}

    def factory(**kw):
        captured.update(kw)
        return FakeClient(_echo_handler, [])

    monkeypatch.setattr(bp, "OpenAI", factory)
    cfg = _remote_cfg(remote_timeout_s=77.0)
    bp.run_prefill(_mk_items(1), cfg, "sys", {})
    assert captured["timeout"] == 77.0
    assert captured["max_retries"] == 0
    assert captured["base_url"] == "http://x/v1"


def test_run_prefill_failed_item_left_uncached_then_serial_fallback(monkeypatch):
    bad_source = "Deliver %s shiny packages to the mayor"
    good_source = "Escort the merchant caravan through the dark forest"

    def handler(kwargs, idx):
        payload = json.loads(kwargs["messages"][1]["content"])
        out = []
        for e in payload:
            if "{0}" in e["text"]:
                out.append({"id": e["id"], "text": "掉了佔位符的譯文"})  # 缺硬性 token
            else:
                out.append({"id": e["id"], "text": "譯" + e["text"]})
        return json.dumps(out, ensure_ascii=False)

    _patch_client(monkeypatch, handler)
    items = [
        bp.PrefillItem(source=bad_source, ck=cache_key(bad_source)),
        bp.PrefillItem(source=good_source, ck=cache_key(good_source)),
    ]
    cfg = _remote_cfg(remote_batch_size=2)
    cache: dict[str, str] = {}
    stats = bp.run_prefill(items, cfg, "sys", cache)

    assert stats.translated == 1 and stats.failed == 1
    assert cache_key(bad_source) not in cache
    assert cache[cache_key(good_source)] == "譯" + good_source

    # 逐檔階段：成功者純快取命中，只有失敗鍵會呼叫序列翻譯
    class RecordingTranslator:
        def __init__(self):
            self.calls = []

        def translate(self, text, cancel_check=None):
            self.calls.append(text)
            return "市長的譯文 {0} 在此"

    tr = RecordingTranslator()
    en = {"quest.bad.desc": bad_source, "quest.good.desc": good_source}
    result, n_translated, n_cached, n_fallback, failed = translate_dict(en, {}, tr, cache)
    assert n_cached == 1
    assert result["quest.good.desc"] == "譯" + good_source
    assert all("{0}" in t or "%s" in t for t in tr.calls)  # 只翻過 bad_source（編碼後）
    assert result["quest.bad.desc"] == "市長的譯文 %s 在此"


def test_run_prefill_parse_failure_retries_once_then_gives_up(monkeypatch):
    calls = _patch_client(monkeypatch, _script_handler(["垃圾回應", "還是垃圾"]))
    items = _mk_items(2)
    cfg = _remote_cfg(remote_batch_size=2)
    cache: dict[str, str] = {}
    stats = bp.run_prefill(items, cfg, "sys", cache)

    assert len(calls) == 2  # 原請求 + 一次解析重試
    assert stats.batches_unparseable == 1
    assert stats.failed == 2 and stats.translated == 0
    assert cache == {}


def test_run_prefill_rate_limit_backs_off_exponentially(monkeypatch):
    script = [_rate_limit_error(), _rate_limit_error(), None]

    def handler(kwargs, idx):
        step = script[min(idx, len(script) - 1)]
        return step if step is not None else _echo_handler(kwargs, idx)

    calls = _patch_client(monkeypatch, handler)
    monkeypatch.setattr(bp.random, "uniform", lambda a, b: 1.0)  # 去抖動，退避可斷言
    sleeps: list[float] = []
    cfg = _remote_cfg(remote_batch_size=1)
    cache: dict[str, str] = {}
    items = _mk_items(1)
    stats = bp.run_prefill(items, cfg, "sys", cache, _sleep=sleeps.append)

    assert len(calls) == 3
    assert stats.translated == 1
    assert cache[items[0].ck].startswith("譯")
    # 第一次退避 2.0s、第二次 4.0s（各以 ≤0.25s 切片睡眠）
    assert sum(sleeps) == pytest.approx(6.0)
    assert max(sleeps) <= 0.25


def test_run_prefill_honors_retry_after_header(monkeypatch):
    script = [_rate_limit_error(headers={"retry-after": "0.5"}), None]

    def handler(kwargs, idx):
        step = script[min(idx, len(script) - 1)]
        return step if step is not None else _echo_handler(kwargs, idx)

    calls = _patch_client(monkeypatch, handler)
    sleeps: list[float] = []
    stats = bp.run_prefill(_mk_items(1), _remote_cfg(remote_batch_size=1), "sys", {},
                           _sleep=sleeps.append)
    assert len(calls) == 2
    assert stats.translated == 1
    assert sum(sleeps) == pytest.approx(0.5)


def test_run_prefill_timeout_is_retryable_not_fatal(monkeypatch):
    # APITimeoutError 是 APIConnectionError（FATAL）的子類——必須被特判為可重試
    timeout_err = APITimeoutError(request=httpx.Request("POST", "http://x"))

    def handler(kwargs, idx):
        return timeout_err if idx == 0 else _echo_handler(kwargs, idx)

    calls = _patch_client(monkeypatch, handler)
    sleeps: list[float] = []
    stats = bp.run_prefill(_mk_items(1), _remote_cfg(remote_batch_size=1), "sys", {},
                           _sleep=sleeps.append)
    assert len(calls) == 2
    assert stats.translated == 1 and stats.failed == 0


def test_run_prefill_non_429_4xx_gives_up_batch_without_retry(monkeypatch):
    req = httpx.Request("POST", "http://x")
    err = BadRequestError("bad request", response=httpx.Response(400, request=req), body=None)
    calls = _patch_client(monkeypatch, _script_handler([err]))
    stats = bp.run_prefill(_mk_items(2), _remote_cfg(remote_batch_size=2), "sys", {})
    assert len(calls) == 1  # 不重試
    assert stats.failed == 2 and stats.translated == 0


def test_run_prefill_fatal_error_aborts_run(monkeypatch):
    req = httpx.Request("POST", "http://x")
    err = AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
    _patch_client(monkeypatch, _script_handler([err]))
    with pytest.raises(TranslatorFatalError):
        bp.run_prefill(_mk_items(3), _remote_cfg(remote_batch_size=1), "sys", {})


def test_run_prefill_cancel_stops_processing(monkeypatch):
    _patch_client(monkeypatch, _echo_handler)
    cache: dict[str, str] = {}
    stats = bp.run_prefill(
        _mk_items(4), _remote_cfg(remote_batch_size=1, remote_concurrency=1), "sys", cache,
        cancel_check=lambda: True,
    )
    assert stats.cancelled is True
    assert stats.translated == 0 and cache == {}


def test_request_batch_raw_cancelled_before_send(monkeypatch):
    calls: list[dict] = []
    client = FakeClient(_echo_handler, calls)
    cancel_event = threading.Event()
    cancel_event.set()
    batch = bp._build_batches(_mk_items(1), batch_size=1)[0]
    out = bp._request_batch_raw(
        client, _remote_cfg(), "m", "sys", batch, 100, cancel_event, lambda s: None
    )
    assert out is None
    assert calls == []


def test_run_prefill_flushes_cache_periodically(monkeypatch):
    _patch_client(monkeypatch, _echo_handler)
    monkeypatch.setattr(bp, "_FLUSH_EVERY", 2)
    flushes: list[int] = []
    cache: dict[str, str] = {}
    bp.run_prefill(
        _mk_items(5), _remote_cfg(remote_batch_size=1, remote_concurrency=1), "sys", cache,
        flush_cache=lambda: flushes.append(len(cache)),
    )
    assert len(flushes) == 2  # 每滿 2 條成功 flush 一次（5 條 → 2 次）


# ---------------------------------------------------------------- 入口包裝

def test_prefill_wrapper_noop_for_local_backend(monkeypatch, tmp_path):
    def _boom(**kw):
        raise AssertionError("local 模式不應建立遠端 client")

    monkeypatch.setattr(bp, "OpenAI", _boom)
    target = _kubejs_target(tmp_path, {"quest.a.desc": "Slay the mighty dragon of the peak"})
    stats = bp.prefill_translation_cache(
        [target], ModelConfig(), "sys", "zh_tw", {},
    )
    assert stats.total_items == 0


def test_prefill_wrapper_noop_when_disabled(monkeypatch, tmp_path):
    def _boom(**kw):
        raise AssertionError("remote_prefill=False 不應建立遠端 client")

    monkeypatch.setattr(bp, "OpenAI", _boom)
    target = _kubejs_target(tmp_path, {"quest.a.desc": "Slay the mighty dragon of the peak"})
    stats = bp.prefill_translation_cache(
        [target], _remote_cfg(remote_prefill=False), "sys", "zh_tw", {},
    )
    assert stats.total_items == 0


def test_prefill_wrapper_end_to_end(monkeypatch, tmp_path):
    calls = _patch_client(monkeypatch, _echo_handler)
    src = "Repair the broken bridge across the river"
    target = _kubejs_target(tmp_path, {"quest.a.desc": src})
    cache: dict[str, str] = {}
    logs: list[str] = []
    stats = bp.prefill_translation_cache(
        [target], _remote_cfg(), "sys", "zh_tw", cache, on_log=logs.append,
    )
    assert stats.translated == 1
    assert cache[cache_key(src)] == "譯" + src
    assert len(calls) == 1
    assert any("批次預翻譯完成" in line for line in logs)
