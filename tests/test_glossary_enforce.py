# tests/test_glossary_enforce.py
from __future__ import annotations

from modpack_translator.pipeline.glossary import Glossary


def _g() -> Glossary:
    return Glossary({
        "Twilight Forest": "暮光森林",
        "Applied Energistics 2": "應用能源2",
        "Create": "機械動力",
    })


def test_multiword_replaced_mid_sentence():
    assert _g().enforce("歡迎來到 Twilight Forest！") == "歡迎來到暮光森林！"


def test_multiword_plural_tolerated():
    assert _g().enforce("探索 Twilight Forests 區域") == "探索暮光森林 區域"


def test_singleword_only_replaced_when_whole_string():
    g = _g()
    assert g.enforce("Create") == "機械動力"
    assert g.enforce("  Create\n") == "  機械動力\n"
    # 句中單字詞不動（避免動詞 create/標題 Create New World 誤傷）
    assert g.enforce("Create New World") == "Create New World"


def test_case_sensitive():
    g = _g()
    assert g.enforce("please create a farm") == "please create a farm"
    assert g.enforce("歡迎來到 twilight forest") == "歡迎來到 twilight forest"


def test_annotation_style_skipped():
    # 譯名已出現在譯文中：視為刻意的中英夾註，不重複替換
    assert _g().enforce("暮光森林(Twilight Forest)入門") == "暮光森林(Twilight Forest)入門"


def test_word_boundary_not_partial():
    g = Glossary({"Aether": "天境"})
    assert g.enforce("Aethersteel 合金") == "Aethersteel 合金"


def test_empty_glossary_noop():
    assert Glossary({}).enforce("Twilight Forest") == "Twilight Forest"
