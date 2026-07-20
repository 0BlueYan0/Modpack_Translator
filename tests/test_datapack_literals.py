"""資料包字面 JSON 在地化（PassiveSkillTree 技能節點、Origins 起源）。

Soulrend 實包最大宗漏翻面：config/paxi/datapacks/soulrend_skilltree 的
985 個技能節點 title + 職業根節點 description 全英文字面；kubejs/data 的
起源 name/description 字面寫死繞過既有 lang 譯文。這些既無 lang 檔也無
locale 覆蓋機制，只能就地把英文字面翻成中文。
"""
import json

from modpack_translator.pipeline import datapack
from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target, read_target_strings


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


class _Boom:
    glossary = None

    def translate(self, text, cancel_check=None):
        raise AssertionError("譯者不應在冪等重跑時被呼叫")


# ── spec 判定 ────────────────────────────────────────────────────────

def test_spec_detection():
    assert datapack.spec_for_path(("data", "skilltree", "skills", "mage.json")) is datapack.SKILLTREE_SKILL
    assert datapack.spec_for_path(("data", "medievalorigins", "origins", "keres.json")) is datapack.ORIGIN
    assert datapack.spec_for_path(("data", "origins", "origin_layers", "rpg_class.json")) is datapack.ORIGIN
    assert datapack.spec_for_path(("data", "skilltree", "skill_trees", "x.json")) is None
    assert datapack.spec_for_path(("data", "foo", "recipes", "x.json")) is None


# ── 抽取：字串 / 富文本元件陣列 ──────────────────────────────────────

def test_extract_skilltree_title_and_description():
    obj = {
        "id": "skilltree:mage",
        "title": "Mage",
        "titleColor": "ab24b3",
        "description": [
            {"f_131101_": {"f_131257_": 8092645}, "text": "Masters of the aether."},
            {"text": "Fragile but powerful."},
        ],
        "bonuses": [{"name": "Skill", "amount": 0.5}],
    }
    got = datapack.extract_text(obj, datapack.SKILLTREE_SKILL)
    assert got == {
        "title": "Mage",
        "description.0.text": "Masters of the aether.",
        "description.1.text": "Fragile but powerful.",
    }
    # bonuses[].name 不抽（屬性修飾名，非顯示標題，避免誤動）
    assert "Skill" not in got.values()


def test_extract_origin_translate_component_skipped():
    obj = {
        "name": "Keres",
        "description": {"translate": "origin.medievalorigins.keres.desc"},
    }
    got = datapack.extract_text(obj, datapack.ORIGIN)
    assert got == {"name": "Keres"}  # translate 元件走 lang 機制，不抽


# ── 回填保結構 ───────────────────────────────────────────────────────

def test_apply_preserves_structure():
    obj = {
        "id": "skilltree:mage",
        "title": "Mage",
        "description": [{"f_131101_": {"f_131257_": 1}, "text": "Masters of the aether."}],
    }
    datapack.apply_text(obj, {"title": "法師", "description.0.text": "乙太的主宰。"})
    assert obj["title"] == "法師"
    assert obj["description"][0]["text"] == "乙太的主宰。"
    assert obj["description"][0]["f_131101_"] == {"f_131257_": 1}  # style 原樣
    assert obj["id"] == "skilltree:mage"


# ── scanner + processor 端到端 ───────────────────────────────────────

def _make_instance(tmp_path):
    root = tmp_path / "minecraft"
    skills = root / "config" / "paxi" / "datapacks" / "soulrend_skilltree" / "data" / "skilltree" / "skills"
    skills.mkdir(parents=True)
    (skills / "mage.json").write_text(json.dumps({
        "id": "skilltree:mage",
        "title": "Mage",
        "description": [{"text": "Masters of the aether."}],
    }), encoding="utf-8")
    (skills / "minor.json").write_text(json.dumps({
        "id": "skilltree:mage-1a",
        "title": "Minor",
    }), encoding="utf-8")
    origins = root / "kubejs" / "data" / "medievalorigins" / "origins"
    origins.mkdir(parents=True)
    (origins / "keres.json").write_text(json.dumps({
        "name": "Keres",
        "description": "You thirst for blood!",
        "powers": ["medievalorigins:keres_heal"],
    }), encoding="utf-8")
    return root


def _datapack_targets(root):
    return [t for t in ModpackScanner().scan(root, "zh_tw", None) if t.format == "datapack_json"]


def test_scan_finds_datapack_literals(tmp_path):
    root = _make_instance(tmp_path)
    targets = _datapack_targets(root)
    names = sorted(t.source_file.name for t in targets)
    assert names == ["keres.json", "mage.json", "minor.json"]
    for t in targets:
        assert t.output_mode == "in_place"
        assert t.target_file == t.source_file  # 就地覆蓋


def test_process_translates_in_place(tmp_path):
    root = _make_instance(tmp_path)
    targets = {t.source_file.name: t for t in _datapack_targets(root)}
    mapping = {
        "Mage": "法師",
        "Masters of the aether.": "乙太的主宰。",
        "Minor": "次要",
        "Keres": "克蕾絲",
        "You thirst for blood!": "你渴求鮮血！",
    }
    for t in targets.values():
        process_target(t, _Dict(mapping), {}, "zh_tw")

    mage = json.loads((root / "config/paxi/datapacks/soulrend_skilltree/data/skilltree/skills/mage.json").read_text(encoding="utf-8"))
    assert mage["title"] == "法師"
    assert mage["description"][0]["text"] == "乙太的主宰。"
    assert mage["id"] == "skilltree:mage"  # 結構欄位不動

    keres = json.loads((root / "kubejs/data/medievalorigins/origins/keres.json").read_text(encoding="utf-8"))
    assert keres["name"] == "克蕾絲"
    assert keres["description"] == "你渴求鮮血！"
    assert keres["powers"] == ["medievalorigins:keres_heal"]  # ID 陣列不動


def test_idempotent_rescan_after_translation(tmp_path):
    root = _make_instance(tmp_path)
    mapping = {
        "Mage": "法師", "Masters of the aether.": "乙太的主宰。", "Minor": "次要",
        "Keres": "克蕾絲", "You thirst for blood!": "你渴求鮮血！",
    }
    for t in _datapack_targets(root):
        process_target(t, _Dict(mapping), {}, "zh_tw")
    # 翻譯後重掃：值皆含 CJK → 不再是目標
    assert _datapack_targets(root) == []


def test_read_target_strings(tmp_path):
    root = _make_instance(tmp_path)
    t = next(t for t in _datapack_targets(root) if t.source_file.name == "mage.json")
    assert read_target_strings(t) == {
        "title": "Mage",
        "description.0.text": "Masters of the aether.",
    }
