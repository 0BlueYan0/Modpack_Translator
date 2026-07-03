from __future__ import annotations

from modpack_translator.pipeline.pack_context import PackContext
from modpack_translator.pipeline.runner import translate_dict


class NamingTranslator:
    """整串返回固定譯名的假 translator。"""

    glossary = None

    def __init__(self, ctx: PackContext, reply: str):
        self.pack_context = ctx
        self._reply = reply

    def translate(self, text, cancel_check=None):
        return self._reply


def test_translate_dict_records_proper_noun_pair():
    ctx = PackContext(root=".")
    tr = NamingTranslator(ctx, "星輝聖所")
    result, *_ = translate_dict({"k": "Starlight Sanctum"}, {}, tr, {})
    assert result == {"k": "星輝聖所"}
    assert ctx.learned_glossary().terms == {"Starlight Sanctum": "星輝聖所"}


def test_translate_dict_does_not_record_sentences():
    ctx = PackContext(root=".")
    tr = NamingTranslator(ctx, "前往星輝聖所並擊敗首領")
    translate_dict({"k": "Go to the sanctum and defeat the boss"}, {}, tr, {})
    assert ctx.learned_glossary() is None


def test_batch_settle_records():
    import threading
    from modpack_translator.pipeline import batch_prefill as bp

    ctx = PackContext(root=".")
    run_ctx = bp._RunContext(
        client=None, cfg=None, model="m", system_prompt="SYS",
        cache={}, stats=bp.PrefillStats(), total=1,
        cancel_event=threading.Event(), cancel_check=None,
        on_progress=None, on_log=None, flush_cache=None,
        sleep=lambda s: None, glossary=None, pack_context=ctx,
    )
    item = bp.PrefillItem(source="Starlight Sanctum", ck="ck1")
    bp._settle(run_ctx, item, "星輝聖所")
    assert ctx.learned_glossary().terms == {"Starlight Sanctum": "星輝聖所"}
    bp._settle(run_ctx, bp.PrefillItem(source="x", ck="ck2"), None)  # 失敗條不爆炸
