"""data/ 側 Patchouli 書（1.18+ datapack 書）掃描。

Patchouli 自 1.18 起書內容載於 data/<ns>/patchouli_books/<book>/<locale>/，
DawnCraft 的 Apotheosis chronicle（118 頁）、untamedwilds encyclopedia
（217 頁）、lilwings（25 頁）全在 data 側，原掃描只認 assets 側 → 整本英文。
"""
import json
import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner

PAGE = json.dumps({
    "name": "Boss Rituals",
    "category": "apotheosis:bosses",
    "pages": [{"type": "patchouli:text", "text": "A mighty foe approaches."}],
})


def _patchouli_targets(jar):
    return [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "patchouli_json"]


def test_data_side_book_detected(tmp_path):
    jar = tmp_path / "apotheosis.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("data/apotheosis/patchouli_books/apoth_chronicle/en_us/entries/bosses.json", PAGE)
        zf.writestr("data/apotheosis/patchouli_books/apoth_chronicle/book.json", json.dumps({"name": "x"}))
    targets = _patchouli_targets(jar)
    assert len(targets) == 1
    t = targets[0]
    assert t.mod_id == "apotheosis"
    assert t.path_in_jar == "data/apotheosis/patchouli_books/apoth_chronicle/en_us/entries/bosses.json"
    assert t.target_path_in_jar == "data/apotheosis/patchouli_books/apoth_chronicle/zh_tw/entries/bosses.json"


def test_data_side_book_existing_translation_skipped(tmp_path):
    zh_page = json.dumps({
        "name": "首領儀式",
        "category": "apotheosis:bosses",
        "pages": [{"type": "patchouli:text", "text": "強敵將至。"}],
    })
    jar = tmp_path / "apotheosis.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("data/apotheosis/patchouli_books/apoth_chronicle/en_us/entries/bosses.json", PAGE)
        zf.writestr("data/apotheosis/patchouli_books/apoth_chronicle/zh_tw/entries/bosses.json", zh_page)
    assert _patchouli_targets(jar) == []


def test_assets_side_book_still_detected(tmp_path):
    jar = tmp_path / "mod.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/somemod/patchouli_books/guide/en_us/entries/intro.json", PAGE)
    targets = _patchouli_targets(jar)
    assert len(targets) == 1
    assert targets[0].target_path_in_jar == "assets/somemod/patchouli_books/guide/zh_tw/entries/intro.json"
