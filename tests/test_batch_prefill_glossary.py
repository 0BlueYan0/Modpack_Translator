from __future__ import annotations

import threading

from modpack_translator.pipeline import batch_prefill as bp
from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.runner import cache_key

G = Glossary({"Twilight Forest": "暮光森林"})


def test_collect_rejects_poisoned_identical_cache(tmp_path):
    # 直接測 collect 的快取判斷邏輯所依賴的守門：
    # 舊「英文→英文」快取命中詞不再視為已翻譯 → 字串會被收集
    from modpack_translator.pipeline.preprocessor import is_usable_translation
    assert is_usable_translation(
        "Twilight Forest", "Twilight Forest",
        accept_identical_proper_noun=True, glossary=G,
    ) is False


def test_process_batch_enforces_leftover_names(monkeypatch):
    item = bp.PrefillItem(source="Welcome to the Twilight Forest!", ck="x")
    enc = bp._EncodedItem(item=item, encoded=item.source, tokens=[])
    monkeypatch.setattr(
        bp, "_request_batch_raw",
        lambda *a, **k: '[{"id": 0, "text": "歡迎來到 Twilight Forest!"}]',
    )
    res = bp._process_batch(
        None, None, "m", "sys", [enc], threading.Event(), lambda s: None, glossary=G,
    )
    assert res.results[0][1] == "歡迎來到暮光森林!"


def test_process_batch_identical_hit_not_accepted(monkeypatch):
    item = bp.PrefillItem(source="Twilight Forest", ck=cache_key("Twilight Forest"))
    enc = bp._EncodedItem(item=item, encoded=item.source, tokens=[])
    monkeypatch.setattr(
        bp, "_request_batch_raw",
        lambda *a, **k: '[{"id": 0, "text": "Twilight Forest"}]',
    )
    res = bp._process_batch(
        None, None, "m", "sys", [enc], threading.Event(), lambda s: None, glossary=G,
    )
    # 原樣返回命中守門 → 該條標記失敗 → 進逐條救援，由 runner 短路以譯名解決
    assert res.results[0][1] is None
