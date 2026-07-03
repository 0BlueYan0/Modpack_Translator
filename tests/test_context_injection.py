from __future__ import annotations

import threading
import types

from modpack_translator.pipeline import batch_prefill as bp
from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.pack_context import PackContext


def test_rescue_translator_injects_learned_terms(monkeypatch):
    ctx = PackContext(root=".")
    ctx.maybe_record("Starlight Sanctum", "星輝聖所", None)
    captured: dict = {}

    def fake_stream(client, cfg, model, messages, max_tokens, cancel_event, sleep):
        captured["system"] = messages[0]["content"]
        return "ok"

    monkeypatch.setattr(bp, "_stream_with_backoff", fake_stream)
    # cfg 需帶 max_tokens：_RescueTranslator.translate 在組實參時會求值 self._cfg.max_tokens，
    # 早於 fake_stream 被呼叫；傳 None 會先 AttributeError。用最小 stub。
    cfg = types.SimpleNamespace(max_tokens=256)
    tr = bp._RescueTranslator(
        None, cfg, "m", "SYS", threading.Event(), lambda s: None,
        glossary=None, pack_context=ctx,
    )
    tr.translate("Enter the Starlight Sanctum")
    assert "Starlight Sanctum = 星輝聖所" in captured["system"]
    assert captured["system"].startswith("SYS")


def test_process_batch_block_includes_learned_terms(monkeypatch):
    ctx = PackContext(root=".")
    ctx.maybe_record("Starlight Sanctum", "星輝聖所", None)
    captured: dict = {}

    def fake_request(client, cfg, model, system_prompt, batch, max_tokens,
                     cancel_event, sleep, glossary_block=""):
        captured["block"] = glossary_block
        return '[{"id": 0, "text": "進入星輝聖所"}]'

    monkeypatch.setattr(bp, "_request_batch_raw", fake_request)
    item = bp.PrefillItem(source="Enter the Starlight Sanctum", ck="x")
    enc = bp._EncodedItem(item=item, encoded=item.source, tokens=[])
    bp._process_batch(
        None, None, "m", "SYS", [enc], threading.Event(), lambda s: None,
        glossary=None, pack_context=ctx,
    )
    assert "Starlight Sanctum = 星輝聖所" in captured["block"]


def test_build_translator_attaches_pack_context():
    from modpack_translator.config import ModelConfig
    from modpack_translator.pipeline.translator import build_translator

    ctx = PackContext(root=".")
    cfg = ModelConfig(backend_mode="remote", remote_base_url="http://x", remote_model="m")
    tr = build_translator(cfg, "SYS", None, pack_context=ctx)
    assert tr.pack_context is ctx
