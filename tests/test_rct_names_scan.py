"""RCT 訓練家名稱偵測：jar 與 kubejs 兩種來源、既有譯檔 diff、冪等。"""
import json
import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner

TRAINER_CYNTHIA = json.dumps({"name": "Champion Cynthia", "team": []})
TRAINER_ALEXA = json.dumps({"name": "Ace Trainer Alexa", "team": []})
KEY_CYNTHIA = "trainer.rctmod.champion_cynthia_1.name"
KEY_ALEXA = "trainer.rctmod.ace_trainer_alexa_0194.name"
LANG_PATH = "assets/rctmod/lang/zh_tw.json"


def _targets(jar):
    return [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "rct_names"]


def _make_jar(tmp_path, extra=None):
    jar = tmp_path / "rctmod.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("data/rctmod/trainers/champion_cynthia_1.json", TRAINER_CYNTHIA)
        zf.writestr("data/rctmod/trainers/ace_trainer_alexa_0194.json", TRAINER_ALEXA)
        for path, payload in (extra or {}).items():
            zf.writestr(path, payload)
    return jar


def test_jar_with_trainers_detected(tmp_path):
    jar = _make_jar(tmp_path)
    ts = _targets(jar)
    assert len(ts) == 1
    t = ts[0]
    assert t.mod_id == "rctmod"
    assert t.output_mode == "jar_inject"
    assert t.path_in_jar == "data/rctmod/trainers/"
    assert t.target_path_in_jar == LANG_PATH
    assert t.existing_path_in_jar is None


def test_jar_without_trainers_not_detected(tmp_path):
    jar = tmp_path / "other.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/other/lang/en_us.json", json.dumps({"a": "A"}))
        # mobs/ 底下是生成設定（RGS 型態），不是名稱定義
        zf.writestr("data/rctmod/mobs/trainers/single/leader_brock_019e.json", "{}")
    assert _targets(jar) == []


def test_fully_translated_names_skipped(tmp_path):
    zh = json.dumps({KEY_CYNTHIA: "冠軍竹蘭", KEY_ALEXA: "精英訓練家艾麗莎"})
    jar = _make_jar(tmp_path, extra={LANG_PATH: zh})
    assert _targets(jar) == []


def test_partially_translated_targets_with_existing(tmp_path):
    zh = json.dumps({KEY_CYNTHIA: "冠軍竹蘭"})
    jar = _make_jar(tmp_path, extra={LANG_PATH: zh})
    ts = _targets(jar)
    assert len(ts) == 1
    assert ts[0].existing_path_in_jar == LANG_PATH


def test_uppercase_existing_lang_detected(tmp_path):
    """既有大寫 zh_TW.json：existing 指向它，寫入目標仍是正規小寫。"""
    zh = json.dumps({KEY_CYNTHIA: "冠軍竹蘭"})
    jar = _make_jar(tmp_path, extra={"assets/rctmod/lang/zh_TW.json": zh})
    ts = _targets(jar)
    assert len(ts) == 1
    assert ts[0].existing_path_in_jar == "assets/rctmod/lang/zh_TW.json"
    assert ts[0].target_path_in_jar == LANG_PATH


def test_include_translated_returns_target(tmp_path):
    zh = json.dumps({KEY_CYNTHIA: "冠軍竹蘭", KEY_ALEXA: "精英訓練家艾麗莎"})
    jar = _make_jar(tmp_path, extra={LANG_PATH: zh})
    scanner = ModpackScanner()
    scanner._include_translated = True
    ts = [t for t in scanner._scan_jar(jar, "zh_tw", None) if t.format == "rct_names"]
    assert len(ts) == 1


def test_kubejs_trainers_detected(tmp_path):
    trainers = tmp_path / "kubejs" / "data" / "rctmod" / "trainers"
    trainers.mkdir(parents=True)
    (trainers / "contentcreators_direwolf20.json").write_text(
        json.dumps({"name": "Direwolf20"}), encoding="utf-8"
    )
    ts = ModpackScanner()._scan_rct_local(tmp_path, "zh_tw", None)
    assert len(ts) == 1
    t = ts[0]
    assert t.output_mode == "in_place"
    assert t.source_file == trainers
    assert t.target_file == tmp_path / "kubejs" / "assets" / "rctmod" / "lang" / "zh_tw.json"
    assert t.existing_file is None


def test_kubejs_fully_translated_skipped(tmp_path):
    trainers = tmp_path / "kubejs" / "data" / "rctmod" / "trainers"
    trainers.mkdir(parents=True)
    (trainers / "allthemods_trainer_lego.json").write_text(
        json.dumps({"name": "Lego"}), encoding="utf-8"
    )
    lang_dir = tmp_path / "kubejs" / "assets" / "rctmod" / "lang"
    lang_dir.mkdir(parents=True)
    (lang_dir / "zh_tw.json").write_text(
        json.dumps({"trainer.rctmod.allthemods_trainer_lego.name": "樂高"}),
        encoding="utf-8",
    )
    assert ModpackScanner()._scan_rct_local(tmp_path, "zh_tw", None) == []
