"""寬鬆 JSON lang 解析：遊戲用 GSON lenient 讀得動的檔，工具也要能讀；
連 GSON 都讀不動的破損檔，改走搶救式抽取——en_us 壞檔讓玩家只看得到
raw key，工具輸出的 zh_tw 是合法 JSON，遊戲讀譯檔反而能正常顯示。

DawnCraft 實例三個真實案例（GSON 讀得動）：
- BrassAmberBattleTowers en_us.json 含 // 註解 → 整包被靜默跳過
- libertyvillagers en_us.json 含 \\' 非法跳脫 → 整包被靜默跳過
- DawnCraft_Resources.zip 的 ftbquests en_us.json 尾逗號 → 資源包覆蓋沒翻

Soulrend 實例七個真實案例：
- ShoulderSurfing / ParticleEffects / Rename Compat quark：字串值含
  原始換行（GSON 照收、Python 嚴格解析拒收）→ 整包被靜默跳過
- medieval_paintings：整檔無外層大括號（GSON 也讀不動）
- Excalibur-JustLevelingFork / Rename Compat alexsdelight：檔案截斷
- Rename Compat spawn：鍵值對間缺逗號
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


def test_non_dict_json_returns_empty():
    assert parse_json_lang('["not", "a", "lang"]') == {}


# ── 搶救式抽取：連 GSON 都讀不動的破損檔，抽出仍完整的鍵值對 ─────────

def test_raw_newline_inside_string_value():
    # ShoulderSurfing / ParticleEffects：GSON 照收原始換行，嚴格 JSON 拒收
    raw = '{"a.key": "line1\nline2", "b.key": "Value"}'
    assert parse_json_lang(raw) == {"a.key": "line1\nline2", "b.key": "Value"}


def test_braceless_key_value_sequence():
    # medieval_paintings：整檔無外層大括號
    raw = (
        '"painting.mp.riders.author": "Community",\r\n'
        '"painting.mp.riders.title": "The Two Riders"\r\n'
    )
    assert parse_json_lang(raw) == {
        "painting.mp.riders.author": "Community",
        "painting.mp.riders.title": "The Two Riders",
    }


def test_truncated_file_salvages_complete_pairs():
    # Excalibur-JustLevelingFork / alexsdelight：檔案中途截斷
    raw = '{\n"a.key": "Value",\n"b.key": "Other",\n"c.key": "Trunc'
    assert parse_json_lang(raw) == {"a.key": "Value", "b.key": "Other"}


def test_missing_comma_between_pairs():
    # Rename Compat spawn：鍵值對間缺逗號（GSON 也讀不動）
    raw = '{"a.key": "Value"\n"b.key": "Other"}'
    assert parse_json_lang(raw) == {"a.key": "Value", "b.key": "Other"}


def test_escapes_survive_salvage():
    raw = '"a.key": "Va\\"lue \\u00a7a\\nend"'  # 無大括號 → 走搶救層
    assert parse_json_lang(raw) == {"a.key": 'Va"lue §a\nend'}


def test_non_string_values_skipped_in_salvage():
    raw = '{"a.key": "Value", "broken": 5\n"b.key": "Other"}'
    assert parse_json_lang(raw) == {"a.key": "Value", "b.key": "Other"}


def test_unrecoverable_garbage_still_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json_lang("total garbage without any pairs")


# ── scanner 整合：帶註解的 en_us.json 不再整包跳過 ────────────────────

def test_scanner_detects_lang_with_comments(tmp_path):
    jar = tmp_path / "ba_bt.jar"
    raw = '{\n\t// Entities\n\t"entity.ba_bt.land_golem": "Entombed Watcher"\n}'
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/ba_bt/lang/en_us.json", raw)
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    assert [t.format for t in targets] == ["json_lang"]
    assert targets[0].path_in_jar == "assets/ba_bt/lang/en_us.json"
