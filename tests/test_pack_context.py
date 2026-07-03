from __future__ import annotations

from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.pack_context import PackContext, load_pack_context


def test_missing_file_gives_empty_context(tmp_path):
    ctx = load_pack_context(tmp_path)
    assert ctx.extra_prompt == ""
    assert ctx.learned_glossary() is None
    assert ctx.learned_count() == 0


def test_corrupt_file_treated_as_empty(tmp_path):
    d = tmp_path / ".modpack_translator"
    d.mkdir()
    (d / "context.json").write_text("not json{{", encoding="utf-8")
    ctx = load_pack_context(tmp_path)
    assert ctx.extra_prompt == ""
    assert ctx.learned_glossary() is None


def test_roundtrip(tmp_path):
    ctx = load_pack_context(tmp_path)
    ctx.extra_prompt = "這是寶可夢主題包，語氣輕鬆"
    assert ctx.maybe_record("Starlight Sanctum", "星輝聖所", None) is True
    ctx.save()
    ctx2 = load_pack_context(tmp_path)
    assert ctx2.extra_prompt == "這是寶可夢主題包，語氣輕鬆"
    assert ctx2.learned_glossary().terms == {"Starlight Sanctum": "星輝聖所"}


def test_record_conditions():
    ctx = PackContext(root=".")
    # 非專有名詞式短語（小寫句子）不記
    assert ctx.maybe_record("go to the sanctum", "前往聖所", None) is False
    # 譯文無 CJK 不記
    assert ctx.maybe_record("Starlight Sanctum", "Sanctum", None) is False
    # 與原文相同不記
    assert ctx.maybe_record("Starlight Sanctum", "Starlight Sanctum", None) is False
    # 已被主用語庫涵蓋不記
    main = Glossary({"Starlight Sanctum": "星輝聖所"})
    assert ctx.maybe_record("Starlight Sanctum", "別的譯法", main) is False
    # 合格才記
    assert ctx.maybe_record("Starlight Sanctum", "星輝聖所", None) is True
    # 重複記錄回 False
    assert ctx.maybe_record("Starlight Sanctum", "星輝聖所", None) is False
    assert ctx.learned_count() == 1


def test_snapshot_rebuilds_after_record():
    ctx = PackContext(root=".")
    assert ctx.learned_glossary() is None
    ctx.maybe_record("Starlight Sanctum", "星輝聖所", None)
    g1 = ctx.learned_glossary()
    assert g1.terms == {"Starlight Sanctum": "星輝聖所"}
    assert ctx.learned_glossary() is g1  # 未變動時重用快照（避免每請求重編 regex）
    ctx.maybe_record("Moonlit Grove", "月光林地", None)
    assert ctx.learned_glossary().terms["Moonlit Grove"] == "月光林地"
