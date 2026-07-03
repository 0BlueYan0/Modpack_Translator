from __future__ import annotations

from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.preprocessor import diff_keys, is_usable_translation

G = Glossary({"Building Gadgets": "建築小工具", "Twilight Forest": "暮光森林"})


def test_identical_hit_rejected_even_with_proper_noun_exemption():
    assert is_usable_translation(
        "Building Gadgets", "Building Gadgets",
        accept_identical_proper_noun=True, glossary=G,
    ) is False


def test_identical_hit_rejected_for_quest_title_key():
    assert is_usable_translation(
        "Building Gadgets", "Building Gadgets",
        key="quest.1A2B3C.title", glossary=G,
    ) is False


def test_identical_miss_keeps_existing_behavior():
    # 未命中用語庫的專有名詞式標題：維持既有放行行為
    assert is_usable_translation(
        "Mining Gadgets", "Mining Gadgets",
        accept_identical_proper_noun=True, glossary=G,
    ) is True


def test_no_glossary_keeps_existing_behavior():
    assert is_usable_translation(
        "Building Gadgets", "Building Gadgets",
        accept_identical_proper_noun=True,
    ) is True


def test_translated_value_not_affected_by_gate():
    assert is_usable_translation("Building Gadgets", "建築小工具", glossary=G) is True


def test_diff_keys_includes_identical_hit_title():
    en = {"quest.1A2B3C.title": "Building Gadgets"}
    zh = {"quest.1A2B3C.title": "Building Gadgets"}
    assert diff_keys(en, zh, glossary=G) == {"quest.1A2B3C.title"}
    assert diff_keys(en, zh) == set()  # 無 glossary 時維持既有行為
