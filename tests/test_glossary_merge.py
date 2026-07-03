# tests/test_glossary_merge.py
from __future__ import annotations

import json

from modpack_translator.pipeline.glossary import (
    load_custom_terms,
    load_merged_glossary,
    save_custom_terms,
)


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_merge_priority_custom_over_modnames_over_official(tmp_path):
    official = _write(tmp_path, "official.json", {"Nether": "地獄", "Creeper": "苦力怕"})
    modnames = _write(tmp_path, "modnames.json", {"Create": "機械動力", "Nether": "官方被蓋"})
    custom = _write(tmp_path, "custom.json", {"Create": "創造模式錯譯修正"})
    g = load_merged_glossary(official, modnames, custom)
    assert g.terms["Creeper"] == "苦力怕"
    assert g.terms["Nether"] == "官方被蓋"        # 模組名層 > 官方層
    assert g.terms["Create"] == "創造模式錯譯修正"  # 自訂層 > 模組名層


def test_custom_empty_translation_deletes_term(tmp_path):
    modnames = _write(tmp_path, "modnames.json", {"Create": "機械動力", "Quark": "夸克"})
    custom = _write(tmp_path, "custom.json", {"create": ""})  # 大小寫不同也要刪
    g = load_merged_glossary(None, modnames, custom)
    assert "Create" not in g.terms
    assert g.terms["Quark"] == "夸克"


def test_missing_files_tolerated(tmp_path):
    modnames = _write(tmp_path, "modnames.json", {"Create": "機械動力"})
    g = load_merged_glossary(tmp_path / "no.json", modnames, tmp_path / "no2.json")
    assert g.terms == {"Create": "機械動力"}
    assert load_merged_glossary(None, None, None) is None


def test_custom_terms_roundtrip(tmp_path):
    p = tmp_path / "sub" / "custom.json"
    save_custom_terms(p, {"Create": "機械動力", "Quark": ""})
    assert load_custom_terms(p) == {"Create": "機械動力", "Quark": ""}
    assert load_custom_terms(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    assert load_custom_terms(tmp_path / "bad.json") == {}


def test_custom_override_is_case_insensitive_keeps_canonical_key(tmp_path):
    modnames = _write(tmp_path, "modnames.json", {"Twilight Forest": "暮色森林"})
    custom = _write(tmp_path, "custom.json", {"twilight forest": "暮光森林"})
    g = load_merged_glossary(None, modnames, custom)
    assert g.terms == {"Twilight Forest": "暮光森林"}  # 單一鍵、保留正式大小寫、採自訂譯名


def test_load_custom_terms_non_dict_returns_empty(tmp_path):
    (tmp_path / "arr.json").write_text("[1, 2]", encoding="utf-8")
    assert load_custom_terms(tmp_path / "arr.json") == {}
