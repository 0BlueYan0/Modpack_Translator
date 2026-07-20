"""the_vault config 在地化（config/the_vault/lang/<locale>/）。

VH 的技能/能力/任務等自訂 GUI 文字不在 lang 檔，而在 config/the_vault/
*.json；模組依遊戲語言載入 lang/<locale>/<同相對路徑> 覆蓋檔。官方出貨
zh_cn/de_de/… 唯獨沒有 zh_tw——此格式產出 lang/zh_tw/ 完整結構檔。
"""
import json

from modpack_translator.pipeline import vh
from modpack_translator.pipeline.runner import process_target
from modpack_translator.pipeline.scanner import ModpackScanner


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


class _Boom:
    glossary = None

    def translate(self, text, cancel_check=None):
        raise AssertionError("translator.translate must NOT be called on idempotent re-run")


_SKILLS = {
    "data": {
        "Dash_Base": {
            "description": [
                {"text": "Propels you a distance forward!", "color": "$text"},
                {"text": "Cast Ability", "color": "$castType"},
            ],
            "current": ["cooldown", "manaCost"],
            "next": ["cooldown", "manaCost"],
        }
    }
}
_QUESTS = {
    "quests": [
        {
            "id": "vault_introduction",
            "type": "the_vault:checkmark",
            "unlockedBy": "root",
            "icon": "the_vault:vault_key",
            "name": "Vault Hunters Introduction",
            "descriptionData": {
                "description": [
                    {"text": "Welcome to Vault Hunters!", "color": "$text"}
                ]
            },
        }
    ]
}


def _make_vh(tmp_path):
    cfg = tmp_path / "config" / "the_vault"
    (cfg / "lang" / "zh_cn").mkdir(parents=True)  # 在地化機制存在的證據
    (cfg / "skill_descriptions.json").write_text(json.dumps(_SKILLS), encoding="utf-8")
    quests = cfg / "quest" / "quests.json"
    quests.parent.mkdir(parents=True)
    quests.write_text(json.dumps(_QUESTS), encoding="utf-8")
    return tmp_path


def _vh_targets(root, include_translated=False):
    targets = ModpackScanner().scan(root, "zh_tw", include_translated=include_translated)
    return [t for t in targets if t.format == "vh_config_json"]


def test_scan_emits_localizable_config_files(tmp_path):
    root = _make_vh(tmp_path)
    targets = {t.source_file.name: t for t in _vh_targets(root)}
    assert set(targets) == {"skill_descriptions.json", "quests.json"}
    skills = targets["skill_descriptions.json"]
    assert skills.mod_id == "the_vault"
    assert skills.output_mode == "in_place"
    assert skills.target_file == root / "config" / "the_vault" / "lang" / "zh_tw" / "skill_descriptions.json"
    quests = targets["quests.json"]
    assert quests.target_file == root / "config" / "the_vault" / "lang" / "zh_tw" / "quest" / "quests.json"


def test_scan_requires_lang_mechanism_dir(tmp_path):
    # 無 config/the_vault/lang/ 的 VH 版本沒有此在地化機制，不產目標
    cfg = tmp_path / "config" / "the_vault"
    cfg.mkdir(parents=True)
    (cfg / "skill_descriptions.json").write_text(json.dumps(_SKILLS), encoding="utf-8")
    assert _vh_targets(tmp_path) == []


def test_process_writes_full_structure_with_translated_text(tmp_path):
    root = _make_vh(tmp_path)
    targets = {t.source_file.name: t for t in _vh_targets(root)}
    process_target(targets["skill_descriptions.json"], _Dict({
        "Propels you a distance forward!": "將你向面朝方向推進一段距離！",
        "Cast Ability": "施放技能",
    }), {}, "zh_tw")
    out = json.loads(
        (root / "config" / "the_vault" / "lang" / "zh_tw" / "skill_descriptions.json")
        .read_text(encoding="utf-8")
    )
    desc = out["data"]["Dash_Base"]["description"]
    assert desc[0]["text"] == "將你向面朝方向推進一段距離！"
    assert desc[1]["text"] == "施放技能"
    # 樣式與統計欄位原樣保留
    assert desc[0]["color"] == "$text"
    assert out["data"]["Dash_Base"]["current"] == ["cooldown", "manaCost"]
    assert out["data"]["Dash_Base"]["next"] == ["cooldown", "manaCost"]
    # 來源根檔不得被動到
    src = json.loads(
        (root / "config" / "the_vault" / "skill_descriptions.json").read_text(encoding="utf-8")
    )
    assert src == _SKILLS


def test_process_quests_translates_name_and_text_only(tmp_path):
    root = _make_vh(tmp_path)
    targets = {t.source_file.name: t for t in _vh_targets(root)}
    process_target(targets["quests.json"], _Dict({
        "Vault Hunters Introduction": "寶庫獵人介紹",
        "Welcome to Vault Hunters!": "歡迎來到寶庫獵人！",
    }), {}, "zh_tw")
    out = json.loads(
        (root / "config" / "the_vault" / "lang" / "zh_tw" / "quest" / "quests.json")
        .read_text(encoding="utf-8")
    )
    quest = out["quests"][0]
    assert quest["name"] == "寶庫獵人介紹"
    assert quest["descriptionData"]["description"][0]["text"] == "歡迎來到寶庫獵人！"
    # 跨檔引用識別字絕不可譯
    assert quest["id"] == "vault_introduction"
    assert quest["unlockedBy"] == "root"
    assert quest["type"] == "the_vault:checkmark"
    assert quest["icon"] == "the_vault:vault_key"


def test_translated_config_is_idempotent(tmp_path):
    root = _make_vh(tmp_path)
    cache: dict[str, str] = {}
    mapping = _Dict({
        "Propels you a distance forward!": "將你向面朝方向推進一段距離！",
        "Cast Ability": "施放技能",
        "Vault Hunters Introduction": "寶庫獵人介紹",
        "Welcome to Vault Hunters!": "歡迎來到寶庫獵人！",
    })
    for t in _vh_targets(root):
        process_target(t, mapping, cache, "zh_tw")
    # 已全數翻譯 → 重掃無目標
    assert _vh_targets(root) == []
    # 同 cache 重跑不送翻、檔案不變
    out_file = root / "config" / "the_vault" / "lang" / "zh_tw" / "skill_descriptions.json"
    before = out_file.read_bytes()
    targets = {t.source_file.name: t for t in _vh_targets(root, include_translated=True)}
    process_target(targets["skill_descriptions.json"], _Boom(), cache, "zh_tw")
    assert out_file.read_bytes() == before


# ── quest 描述段 "\n\n" 邊緣空白修復 ────────────────────────────────────

_QUESTS_EDGED = {
    "quests": [
        {
            "id": "vault_introduction",
            "name": "Vault Hunters Introduction",
            "descriptionData": {
                "description": [
                    {"text": "Welcome to Vault Hunters!", "color": "$text"},
                    {"text": "\n\nThis modpack turns your game around.", "color": "$text"},
                ]
            },
        }
    ]
}


def test_edge_whitespace_repair_without_api(tmp_path):
    """既有譯文遺失原文 "\n\n" 前綴 → 掃描應標記、runner 零 API 修復。"""
    root = _make_vh(tmp_path)
    cfg = root / "config" / "the_vault"
    (cfg / "quest" / "quests.json").write_text(json.dumps(_QUESTS_EDGED), encoding="utf-8")
    translated = json.loads(json.dumps(_QUESTS_EDGED))
    quest = translated["quests"][0]
    quest["name"] = "寶庫獵人介紹"
    desc = quest["descriptionData"]["description"]
    desc[0]["text"] = "歡迎來到寶庫獵人！"
    desc[1]["text"] = "這個模組包會顛覆你的遊戲。"  # 前綴 \n\n 遺失
    out = cfg / "lang" / "zh_tw" / "quest" / "quests.json"
    out.parent.mkdir(parents=True)
    out.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")

    targets = [t for t in _vh_targets(root) if t.source_file.name == "quests.json"]
    assert targets, "邊緣空白不符應被掃出"
    process_target(targets[0], _Boom(), {}, "zh_tw")  # 不得呼叫 API
    repaired = json.loads(out.read_text(encoding="utf-8"))
    desc = repaired["quests"][0]["descriptionData"]["description"]
    assert desc[1]["text"] == "\n\n這個模組包會顛覆你的遊戲。"
    assert desc[0]["text"] == "歡迎來到寶庫獵人！"
    # 修復後冪等
    assert [t for t in _vh_targets(root) if t.source_file.name == "quests.json"] == []


def test_preserve_edges():
    assert vh.preserve_edges("\n\nHello.", "你好。") == "\n\n你好。"
    assert vh.preserve_edges("Hello. ", "你好。") == "你好。 "
    assert vh.preserve_edges("Hello.", "你好。") == "你好。"
    assert vh.preserve_edges("\n\nHello.", "\n\n你好。") == "\n\n你好。"
    assert vh.preserve_edges("\n\nHello.", "   ") == "   "


# ── 平台字集亂碼防護：輸出一律 \uXXXX 跳脫純 ASCII ──────────────────────
# the_vault 以單參數 FileReader（平台預設字集，Windows=MS950/GBK）讀
# config 在地化檔——原始 UTF-8 中文在遊戲內必亂碼；GSON 會還原跳脫。

def test_output_is_pure_ascii_escaped(tmp_path):
    root = _make_vh(tmp_path)
    targets = {t.source_file.name: t for t in _vh_targets(root)}
    process_target(targets["quests.json"], _Dict({
        "Vault Hunters Introduction": "寶庫獵人介紹",
        "Welcome to Vault Hunters!": "歡迎來到寶庫獵人！",
    }), {}, "zh_tw")
    out = root / "config" / "the_vault" / "lang" / "zh_tw" / "quest" / "quests.json"
    raw = out.read_bytes()
    assert all(b <= 0x7F for b in raw), "輸出必須是純 ASCII（\\uXXXX 跳脫）"
    assert json.loads(raw.decode("ascii"))["quests"][0]["name"] == "寶庫獵人介紹"


def test_raw_utf8_existing_output_flagged_and_reencoded(tmp_path):
    """舊版寫出的原始 UTF-8 中文檔（遊戲內亂碼）→ 掃描標記、零 API 重編碼。"""
    root = _make_vh(tmp_path)
    translated = json.loads(json.dumps(_QUESTS))
    translated["quests"][0]["name"] = "寶庫獵人介紹"
    translated["quests"][0]["descriptionData"]["description"][0]["text"] = "歡迎來到寶庫獵人！"
    out = root / "config" / "the_vault" / "lang" / "zh_tw" / "quest" / "quests.json"
    out.parent.mkdir(parents=True)
    out.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")
    assert any(b > 0x7F for b in out.read_bytes())

    targets = [t for t in _vh_targets(root) if t.source_file.name == "quests.json"]
    assert targets, "原始非 ASCII 輸出應被掃出重編碼"
    process_target(targets[0], _Boom(), {}, "zh_tw")  # 不得呼叫 API
    raw = out.read_bytes()
    assert all(b <= 0x7F for b in raw)
    assert json.loads(raw.decode("ascii"))["quests"][0]["name"] == "寶庫獵人介紹"
    # 重編碼後冪等
    assert [t for t in _vh_targets(root) if t.source_file.name == "quests.json"] == []


# ── translations.json 就地翻譯（MixinClientLanguage 語言表注入來源） ─────

_TRANSLATIONS = {
    "translations": {
        "the_vault.boss_rune_effect.light_melee": "Melee Attack",
        "the_vault.boss_rune_effect.health": "Health",
    }
}


def test_translations_json_translated_in_place(tmp_path):
    root = _make_vh(tmp_path)
    cfg = root / "config" / "the_vault"
    tj = cfg / "translations.json"
    tj.write_text(json.dumps(_TRANSLATIONS), encoding="utf-8")

    targets = [t for t in _vh_targets(root) if t.source_file.name == "translations.json"]
    assert len(targets) == 1
    target = targets[0]
    assert target.target_file == tj  # 來源即目標
    process_target(target, _Dict({
        "Melee Attack": "近戰攻擊",
        "Health": "生命值",
    }), {}, "zh_tw")
    out = json.loads(tj.read_text(encoding="utf-8"))
    assert out["translations"]["the_vault.boss_rune_effect.light_melee"] == "近戰攻擊"
    assert out["translations"]["the_vault.boss_rune_effect.health"] == "生命值"
    # 值已含 CJK → 冪等，重掃無目標
    assert [t for t in _vh_targets(root) if t.source_file.name == "translations.json"] == []


def test_scan_emits_sky_quests(tmp_path):
    root = _make_vh(tmp_path)
    cfg = root / "config" / "the_vault"
    sky = cfg / "quest" / "sky_quests.json"
    sky.write_text(json.dumps(_QUESTS), encoding="utf-8")
    targets = {t.source_file.name: t for t in _vh_targets(root)}
    assert "sky_quests.json" in targets
    assert targets["sky_quests.json"].target_file == (
        root / "config" / "the_vault" / "lang" / "zh_tw" / "quest" / "sky_quests.json"
    )


# ── 各檔 spec 抽取規則（不落地檔案的單元驗證） ──────────────────────────

def test_extract_trials_screen_list_and_text_fields():
    spec = vh.LOCALIZABLE_FILES["greed/trials_screen.json"]
    data = {
        "trialWarningText": ["You are about to enter.", "Good luck."],
        "styles": [{"text": "Begin Trial", "color": "#ffffff"}],
    }
    out = vh.extract_text(data, spec)
    assert set(out.values()) == {"You are about to enter.", "Good luck.", "Begin Trial"}


def test_extract_all_values_specs():
    spec = vh.LOCALIZABLE_FILES["menu_player_stat_description.json"]
    data = {"PROMINENT": {"the_vault:armor": "<gray>Your armor.", "health": "<gray>Your health."}}
    out = vh.extract_text(data, spec)
    assert set(out.values()) == {"<gray>Your armor.", "<gray>Your health."}
    # 資源 ID 鍵的 JSON path 可寫回
    for key, value in out.items():
        vh.apply_text(data, key, value + "!")
    assert data["PROMINENT"]["the_vault:armor"] == "<gray>Your armor.!"


def test_extract_tooltip_value_not_item():
    spec = vh.LOCALIZABLE_FILES["tooltip.json"]
    data = {"tooltips": [{"item": "the_vault:vault_diamond", "value": "A shiny gem."}]}
    out = vh.extract_text(data, spec)
    assert list(out.values()) == ["A shiny gem."]
