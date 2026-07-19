"""寬鬆 JSON lang 解析：遊戲用 GSON lenient 讀得動的檔，工具也要能讀。

DawnCraft 實例三個真實案例：
- BrassAmberBattleTowers en_us.json 含 // 註解 → 整包被靜默跳過
- libertyvillagers en_us.json 含 \\' 非法跳脫 → 整包被靜默跳過
- DawnCraft_Resources.zip 的 ftbquests en_us.json 尾逗號 → 資源包覆蓋沒翻
"""
import json
import zipfile

import pytest

from modpack_translator.pipeline.preprocessor import parse_json_lang
from modpack_translator.pipeline.scanner import ModpackScanner


# ── 三種 GSON lenient 可讀的破損樣態 ─────────────────────────────────

def test_line_comments_are_tolerated():
    raw = (
        '{\n'
        '\t"block.ba_bt.tab_icon": "BrassAmber Battletowers",\n'
        '\n'
        '\t// Entities\n'
        '\t"entity.ba_bt.land_golem": "Bahryn\'muul, Entombed Watcher"\n'
        '}\n'
    )
    assert parse_json_lang(raw) == {
        "block.ba_bt.tab_icon": "BrassAmber Battletowers",
        "entity.ba_bt.land_golem": "Bahryn'muul, Entombed Watcher",
    }


def test_block_comments_are_tolerated():
    raw = '{ /* header\n comment */ "a.key": "Value" }'
    assert parse_json_lang(raw) == {"a.key": "Value"}


def test_invalid_escape_is_tolerated():
    raw = '{"text.LibertyVillagers.villagerStats.title": "Liberty\\\'s Villager Stats"}'
    assert parse_json_lang(raw) == {
        "text.LibertyVillagers.villagerStats.title": "Liberty's Villager Stats"
    }


def test_trailing_commas_are_tolerated():
    raw = '{"a.key": "Value", "b.key": "Other",}'
    assert parse_json_lang(raw) == {"a.key": "Value", "b.key": "Other"}


# ── 不得影響正常內容 ─────────────────────────────────────────────────

def test_slashes_and_escapes_inside_strings_survive():
    raw = json.dumps({
        "a.url": "https://example.com/path // not a comment",
        "a.newline": "line1\nline2",
        "a.quote": 'say "hi"',
        "a.backslash": "C:\\mods",
        "a.unicode": "§aGreen",
    })
    assert parse_json_lang(raw) == {
        "a.url": "https://example.com/path // not a comment",
        "a.newline": "line1\nline2",
        "a.quote": 'say "hi"',
        "a.backslash": "C:\\mods",
        "a.unicode": "§aGreen",
    }


def test_truly_broken_json_still_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json_lang('{"a": "b" "c": "d"}')  # 缺逗號連 GSON 都讀不動


def test_non_dict_json_returns_empty():
    assert parse_json_lang('["not", "a", "lang"]') == {}


# ── scanner 整合：帶註解的 en_us.json 不再整包跳過 ────────────────────

def test_scanner_detects_lang_with_comments(tmp_path):
    jar = tmp_path / "ba_bt.jar"
    raw = '{\n\t// Entities\n\t"entity.ba_bt.land_golem": "Entombed Watcher"\n}'
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/ba_bt/lang/en_us.json", raw)
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    assert [t.format for t in targets] == ["json_lang"]
    assert targets[0].path_in_jar == "assets/ba_bt/lang/en_us.json"
