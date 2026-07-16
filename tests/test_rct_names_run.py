"""RCT 訓練家名稱端到端：靜態譯名零 API、職業拆譯、既有 lang 合併、冪等。"""
import json
import zipfile

from modpack_translator.pipeline import rct
from modpack_translator.pipeline.preprocessor import classify_translation_entry
from modpack_translator.pipeline.runner import process_target, translate_dict
from modpack_translator.pipeline.scanner import ModpackScanner

LANG_PATH = "assets/rctmod/lang/zh_tw.json"


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls: list[str] = []

    def translate(self, text, cancel_check=None):
        self.calls.append(text)
        return self.mapping.get(text.strip(), text)


class _Boom:
    glossary = None

    def translate(self, text, cancel_check=None):
        raise AssertionError(f"translator.translate must NOT be called (got {text!r})")


def _rct_targets(jar):
    return [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "rct_names"]


def _make_jar(tmp_path):
    jar = tmp_path / "rctmod.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/rctmod/lang/en_us.json", json.dumps({"gui.rctmod.trainer_card.title": "Trainer Card"}))
        zf.writestr(LANG_PATH, json.dumps({"gui.rctmod.trainer_card.title": "訓練家卡"}))
        zf.writestr("data/rctmod/trainers/champion_cynthia_1.json", json.dumps({"name": "Champion Cynthia"}))
        zf.writestr("data/rctmod/trainers/leader_brock_019e.json", json.dumps({"name": "Leader Brock"}))
        zf.writestr("data/rctmod/trainers/ace_trainer_alexa_0194.json", json.dumps({"name": "Ace Trainer Alexa"}))
        zf.writestr("data/rctmod/trainers/ace_trainer_alexa_05d7.json", json.dumps({"name": "Ace Trainer Alexa"}))
    return jar


def test_process_writes_name_keys_and_keeps_lang(tmp_path):
    jar = _make_jar(tmp_path)
    [t] = _rct_targets(jar)
    translator = _Dict({"Alexa": "艾麗莎"})
    n_t, n_c, n_f, failed = process_target(t, translator, {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        out = json.loads(zf.read(LANG_PATH).decode("utf-8-sig"))
    # 靜態官方譯名（零 API）
    assert out["trainer.rctmod.champion_cynthia_1.name"] == "冠軍竹蘭"
    assert out["trainer.rctmod.leader_brock_019e.name"] == "館主小剛"
    # 職業拆譯：職業確定性翻譯＋人名模型音譯；同名兩隻共用同一譯法（快取）
    assert out["trainer.rctmod.ace_trainer_alexa_0194.name"] == "精英訓練家艾麗莎"
    assert out["trainer.rctmod.ace_trainer_alexa_05d7.name"] == "精英訓練家艾麗莎"
    # 模型只收到人名段、且同名只送一次（第二次快取命中）
    assert translator.calls == ["Alexa"]
    # 既有 lang 翻譯原封不動
    assert out["gui.rctmod.trainer_card.title"] == "訓練家卡"
    assert not failed


def test_static_names_need_no_model(tmp_path):
    jar = tmp_path / "rctmod.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("data/rctmod/trainers/elite_four_flint_1.json", json.dumps({"name": "Elite Four Flint"}))
        zf.writestr("data/rctmod/trainers/flint_2.json", json.dumps({"name": "Flint"}))
    [t] = _rct_targets(jar)
    process_target(t, _Boom(), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        out = json.loads(zf.read(LANG_PATH).decode("utf-8-sig"))
    # 訓練家 Flint 是四天王大葉，絕不能沿用一般詞彙「燧石」
    assert out["trainer.rctmod.elite_four_flint_1.name"] == "四天王大葉"
    assert out["trainer.rctmod.flint_2.name"] == "大葉"


def test_idempotent_rerun_no_model(tmp_path):
    jar = _make_jar(tmp_path)
    cache: dict[str, str] = {}
    [t] = _rct_targets(jar)
    process_target(t, _Dict({"Alexa": "艾麗莎"}), cache, "zh_tw")
    assert _rct_targets(jar) == []                 # 已全譯 → 重掃 0 目標
    process_target(t, _Boom(), cache, "zh_tw")     # 重跑不得送翻


def test_kubejs_names_written_to_kubejs_assets(tmp_path):
    trainers = tmp_path / "kubejs" / "data" / "rctmod" / "trainers"
    trainers.mkdir(parents=True)
    (trainers / "contentcreators_direwolf20.json").write_text(
        json.dumps({"name": "Direwolf20"}), encoding="utf-8"
    )
    [t] = ModpackScanner()._scan_rct_local(tmp_path, "zh_tw", None)
    # 玩家 ID model 原樣返回是正確判斷（專有名詞豁免）
    translator = _Dict({})
    process_target(t, translator, {}, "zh_tw")
    out = json.loads(
        (tmp_path / "kubejs" / "assets" / "rctmod" / "lang" / "zh_tw.json").read_text(encoding="utf-8")
    )
    assert out["trainer.rctmod.contentcreators_direwolf20.name"] == "Direwolf20"


def test_split_class_and_compose():
    assert rct.split_class("Ace Trainer Alexa") == ("精英訓練家", "Alexa")
    assert rct.split_class("Swimmer♀ Tiffany") == ("泳裝女孩", "Tiffany")
    assert rct.split_class("Pokémon Ranger Allison") == ("寶可夢巡護員", "Allison")
    assert rct.split_class("Team Rocket Grunt Zoey") == ("火箭隊", "Grunt Zoey")
    assert rct.split_class("Double Team Jen & Zac") is None
    assert rct.compose("精英訓練家", "艾麗莎") == "精英訓練家艾麗莎"
    assert rct.compose("冠軍", "Jax") == "冠軍 Jax"
    assert rct.compose("四天王", "") == "四天王"


def test_model_source_matches_pipeline():
    assert rct.model_source("Champion Cynthia") is None          # 靜態命中
    assert rct.model_source("Ace Trainer Alexa") == "Alexa"      # 職業拆譯
    assert rct.model_source("Direwolf20") == "Direwolf20"        # 整串交模型
    assert rct.model_source("Twins") is None                     # 職業即整串


def test_e4_trainer_type_static_translation():
    """trainer_type.rctmod.e4.title = "E4" 值樣貌像代號，先前被不可譯過濾器
    整鍵排除；靜態譯表豁免後以固定譯文「四天王」直接補上，不呼叫模型。"""
    key = "trainer_type.rctmod.e4.title"
    assert classify_translation_entry(key, "E4") == "translate"
    result, n_t, n_c, n_f, failed = translate_dict({key: "E4"}, {}, _Boom(), {})
    assert result[key] == "四天王"
    assert not failed
