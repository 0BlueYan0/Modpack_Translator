"""data/<ns>/lang/en_us.json(伺服端在地化)掃描。

Open Parties and Claims 把伺服端訊息的 lang 放在 data/ 側(493 鍵,含
聊天指令回覆),與 assets/ 側同構;只翻 assets 側時伺服端訊息仍是英文。
zh_tw 寫回 data/<ns>/lang/zh_tw.json,字串與 assets 側相同 → 快取全命中,
零額外 API 成本。
"""
import json
import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


def _lang_targets(jar):
    return [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "json_lang"]


def test_scan_finds_data_side_lang(tmp_path):
    jar = tmp_path / "opac.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("data/openpartiesandclaims/lang/en_us.json", '{"gui.xaero_parties_player": "Player:"}')
    [t] = _lang_targets(jar)
    assert t.path_in_jar == "data/openpartiesandclaims/lang/en_us.json"
    assert t.target_path_in_jar == "data/openpartiesandclaims/lang/zh_tw.json"
    assert t.mod_id == "openpartiesandclaims"


def test_data_and_assets_sides_are_separate_targets(tmp_path):
    jar = tmp_path / "opac.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/openpartiesandclaims/lang/en_us.json", '{"k": "Player:"}')
        zf.writestr("data/openpartiesandclaims/lang/en_us.json", '{"k": "Player:"}')
    targets = _lang_targets(jar)
    assert {t.path_in_jar for t in targets} == {
        "assets/openpartiesandclaims/lang/en_us.json",
        "data/openpartiesandclaims/lang/en_us.json",
    }


def test_data_side_translated_and_idempotent(tmp_path):
    jar = tmp_path / "opac.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("data/openpartiesandclaims/lang/en_us.json", '{"gui.k": "Current Party:"}')
    [t] = _lang_targets(jar)
    process_target(t, _Dict({"Current Party:": "目前隊伍:"}), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        out = json.loads(zf.read("data/openpartiesandclaims/lang/zh_tw.json").decode("utf-8-sig"))
    assert out == {"gui.k": "目前隊伍:"}
    assert _lang_targets(jar) == []


def test_random_data_json_named_en_us_not_lang_is_ignored(tmp_path):
    """data 側名為 en_us.json 但不是 lang 結構(值非字串)→ 解析為空,不成目標。"""
    jar = tmp_path / "weird.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("data/somemod/lang/en_us.json", '{"nested": {"a": 1}, "n": 3}')
    assert _lang_targets(jar) == []
