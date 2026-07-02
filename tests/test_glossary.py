from __future__ import annotations

import sys
from pathlib import Path

# scripts/ 是 namespace package，需要專案根目錄在 sys.path 上
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_glossary import build_glossary_map
from modpack_translator.pipeline.glossary import (
    _BATCH_TERM_CAP,
    Glossary,
    augment_prompt,
    available_glossaries,
    load_glossary,
)

_TERMS = {
    "Nether": "地獄",
    "Nether Star": "地獄之星",
    "Shulker Box": "界伏蚌盒",
    "Blast Furnace": "高爐",
    "Fire": "火",
}


def _glossary() -> Glossary:
    return Glossary(dict(_TERMS))


# ────────────────────────────────────────────── build_glossary_map


def test_build_map_basic_and_skips():
    en = {
        "block.minecraft.stone": "Stone",
        "block.minecraft.spawn.not_valid": "You have no home bed",  # 深層 key 排除
        "block.minecraft.banner.base.black": "Fully Black Field",   # 深層 key 排除
        "item.minecraft.bundle": "Bundle",
        "item.minecraft.tnt": "TNT",                                 # en==zh 跳過
        "gui.done": "Done",                                          # 前綴不符
        "item.minecraft.written_book.author": "by %s",               # 佔位符跳過
        "effect.minecraft.absorption": "Absorption",                 # zh 缺漏跳過
    }
    zh = {
        "block.minecraft.stone": "石頭",
        "block.minecraft.spawn.not_valid": "你沒有床或家",
        "block.minecraft.banner.base.black": "黑色底",
        "item.minecraft.bundle": "束口袋",
        "item.minecraft.tnt": "TNT",
        "gui.done": "完成",
        "item.minecraft.written_book.author": "由 %s 所著",
    }
    result = build_glossary_map(en, zh)
    assert result == {"Bundle": "束口袋", "Stone": "石頭"}


def test_build_map_deep_prefix_allowlist_keeps_potions_and_attributes():
    en = {
        "item.minecraft.potion.effect.swiftness": "Potion of Swiftness",
        "attribute.name.generic.armor": "Armor",
    }
    zh = {
        "item.minecraft.potion.effect.swiftness": "迅捷藥水",
        "attribute.name.generic.armor": "護甲",
    }
    result = build_glossary_map(en, zh)
    assert result == {"Armor": "護甲", "Potion of Swiftness": "迅捷藥水"}


def test_build_map_conflict_resolved_by_prefix_priority():
    # entity 優先於 effect：Wither 取「凋零怪」；effect 優先於 attribute：Speed 取「加速」
    en = {
        "effect.minecraft.wither": "Wither",
        "entity.minecraft.wither": "Wither",
        "attribute.name.generic.movement_speed": "Speed",
        "effect.minecraft.speed": "Speed",
    }
    zh = {
        "effect.minecraft.wither": "凋零",
        "entity.minecraft.wither": "凋零怪",
        "attribute.name.generic.movement_speed": "速度",
        "effect.minecraft.speed": "加速",
    }
    result = build_glossary_map(en, zh)
    assert result == {"Speed": "加速", "Wither": "凋零怪"}


def test_build_map_extra_keys_include_dimension_names():
    en = {
        "advancements.nether.root.title": "Nether",
        "flat_world_preset.minecraft.overworld": "Overworld",
        "advancements.story.root.title": "Minecraft",  # 不在白名單
    }
    zh = {
        "advancements.nether.root.title": "地獄",
        "flat_world_preset.minecraft.overworld": "主世界",
        "advancements.story.root.title": "Minecraft",
    }
    result = build_glossary_map(en, zh)
    assert result == {"Nether": "地獄", "Overworld": "主世界"}


def test_build_map_skips_short_terms():
    en = {"item.minecraft.ab": "Ab"}
    zh = {"item.minecraft.ab": "某"}
    assert build_glossary_map(en, zh) == {}


# ────────────────────────────────────────────── Glossary.match_terms


def test_match_longest_term_wins():
    pairs = _glossary().match_terms(["Bring a Nether Star home"])
    assert ("Nether Star", "地獄之星") in pairs
    assert ("Nether", "地獄") not in pairs


def test_match_word_boundary_blocks_networking():
    assert _glossary().match_terms(["Networking is fun"]) == []


def test_match_case_insensitive_and_plural():
    pairs = _glossary().match_terms(["craft 3 blast furnaces and two shulker boxes"])
    assert ("Blast Furnace", "高爐") in pairs
    assert ("Shulker Box", "界伏蚌盒") in pairs


def test_match_on_placeholder_encoded_text():
    pairs = _glossary().match_terms(["{0}Go to the Nether{1} now"])
    assert pairs == [("Nether", "地獄")]


def test_match_dedupes_across_texts_and_sorts_specific_first():
    pairs = _glossary().match_terms(["Nether Star", "the Fire", "more Fire"])
    assert pairs == [("Nether Star", "地獄之星"), ("Fire", "火")]


def test_match_empty_terms_no_crash():
    assert Glossary({}).match_terms(["Nether"]) == []


# ────────────────────────────────────────────── exact_match / format_block / augment_prompt


def test_exact_match_preserves_whitespace_and_case_insensitive():
    g = _glossary()
    assert g.exact_match("Nether") == "地獄"
    assert g.exact_match("  nether \n") == "  地獄 \n"
    assert g.exact_match("Nether Fortress") is None
    assert g.exact_match("Go to the Nether") is None
    assert g.exact_match("   ") is None


def test_format_block_caps_terms():
    g = _glossary()
    pairs = [(f"Term {i:02d}", f"譯{i}") for i in range(20)]
    block = g.format_block(pairs, cap=8)
    assert block.count("=") == 8
    assert "[Glossary]" in block


def test_format_block_char_budget():
    g = _glossary()
    pairs = [(f"Term {i:03d} " + "x" * 300, "譯") for i in range(_BATCH_TERM_CAP)]
    block = g.format_block(pairs, cap=_BATCH_TERM_CAP)
    assert len(block) <= 2000


def test_augment_prompt_none_and_no_match_are_noop():
    assert augment_prompt("sys", None, ["Nether"]) == "sys"
    assert augment_prompt("sys", _glossary(), ["nothing here"]) == "sys"


def test_augment_prompt_appends_block_at_end():
    out = augment_prompt("sys", _glossary(), ["Enter the Nether"])
    assert out.startswith("sys\n\n[Glossary]")
    assert "Nether = 地獄" in out


# ────────────────────────────────────────────── load_glossary / available_glossaries


def test_load_glossary_missing_and_invalid(tmp_path):
    assert load_glossary(None) is None
    assert load_glossary("") is None
    assert load_glossary(tmp_path / "missing.json") is None

    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert load_glossary(bad) is None

    not_dict = tmp_path / "list.json"
    not_dict.write_text("[1, 2]", encoding="utf-8")
    assert load_glossary(not_dict) is None

    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert load_glossary(empty) is None


def test_load_glossary_valid_filters_non_string(tmp_path):
    p = tmp_path / "g.json"
    p.write_text('{"Nether": "地獄", "Bad": 1, "": "x", "Blank": " "}', encoding="utf-8")
    g = load_glossary(p)
    assert g is not None
    assert g.terms == {"Nether": "地獄"}


def test_available_glossaries_sorted_desc(tmp_path):
    (tmp_path / "zh_tw_1.20.1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "zh_tw_1.21.1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "zh_tw_1.9.json").write_text("{}", encoding="utf-8")
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")
    found = available_glossaries("zh_tw", tmp_path)
    assert [v for v, _ in found] == ["1.21.1", "1.20.1", "1.9"]
    assert available_glossaries("zh_tw", tmp_path / "nope") == []
