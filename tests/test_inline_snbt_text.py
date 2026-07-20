"""FTBQ inline snbt 抽取修正（Soulrend 實包，v1.13.0）。

三個實測漏抽樣態（config/ftbquests/quests/chapters/）：
1. the_realms.snbt：description 陣列的字串值含 ] （按鍵提示 [O]、
   [Middle-Button]）——非貪婪 regex 在字串內的 ] 提早終止陣列範圍，
   後續整段英文句子全部漏抽。改為引號感知的括號深度掃描。
2. dragonslayer.snbt：字串化 JSON 元件行（"{\"text\":\"...\",\"align\":
   \"center\"}" 圖說）被 { 開頭規則整行排除。改為解析出 text 欄位翻譯、
   寫回時重組完整元件。
3. 多詞方括號標題 "[Read Me]" 被 [ 開頭規則排除。單一 token 鍵提示
   （[shift]、[O]）維持不翻。
"""
import json

from modpack_translator.pipeline.preprocessor import (
    read_inline_snbt_text,
    replace_inline_snbt_text,
)


def _write(tmp_path, raw):
    f = tmp_path / "chapter.snbt"
    f.write_text(raw, encoding="utf-8")
    return f


# ── 1. 字串值內的 ] 不得截斷 description 陣列 ──────────────────────────

THE_REALMS_LIKE = '''{
	quests: [{
		description: [
			"Press &d[&lMiddle-Button&r&d]&r (mouse) to lock-on to enemies"
			""
			"With the special combat mode you will harness special weapon skills."
		]
		id: "55A21D1598641602"
		title: "&l&bEpic Fight"
	}]
}
'''


def test_bracket_inside_string_does_not_truncate_array(tmp_path):
    strings = read_inline_snbt_text(_write(tmp_path, THE_REALMS_LIKE))
    values = set(strings.values())
    assert "Press &d[&lMiddle-Button&r&d]&r (mouse) to lock-on to enemies" in values
    assert "With the special combat mode you will harness special weapon skills." in values
    assert "&l&bEpic Fight" in values


def test_replace_after_bracket_array(tmp_path):
    f = _write(tmp_path, THE_REALMS_LIKE)
    strings = read_inline_snbt_text(f)
    key = next(k for k, v in strings.items() if v.startswith("With the special"))
    out = replace_inline_snbt_text(
        f.read_text(encoding="utf-8"), {key: "特殊戰鬥模式讓你施展專屬武器技能。"}
    )
    assert '"特殊戰鬥模式讓你施展專屬武器技能。"' in out
    assert "Press &d[&lMiddle-Button&r&d]&r (mouse) to lock-on to enemies" in out


# ── 2. 字串化 JSON 元件行：抽 text 欄位、寫回重組 ─────────────────────

COMPONENT_LINE = (
    '{\n\tquests: [{\n\t\tdescription: [\n'
    '\t\t\t"{image:soulrend:textures/quests/egg.png width:200 align:center}"\n'
    '\t\t\t"{\\"text\\":\\"A lighting dragon egg hatching.\\",\\"align\\":\\"center\\"}"\n'
    '\t\t]\n\t\tid: "6D4E46958716B057"\n\t}]\n}\n'
)


def test_component_json_line_text_extracted(tmp_path):
    strings = read_inline_snbt_text(_write(tmp_path, COMPONENT_LINE))
    assert "A lighting dragon egg hatching." in strings.values()
    # {image:...} 標記行仍不可譯
    assert not any("image:" in v for v in strings.values())


def test_component_json_line_replace_keeps_structure(tmp_path):
    f = _write(tmp_path, COMPONENT_LINE)
    strings = read_inline_snbt_text(f)
    key = next(k for k, v in strings.items() if "dragon egg" in v)
    out = replace_inline_snbt_text(
        f.read_text(encoding="utf-8"), {key: "一顆正在孵化的閃電龍蛋。"}
    )
    # 寫回後該行仍是合法 JSON 元件字串，align 保留
    line = next(l for l in out.splitlines() if "align" in l and "閃電龍蛋" in l)
    inner = json.loads(json.loads("[" + line.strip() + "]")[0])
    assert inner == {"text": "一顆正在孵化的閃電龍蛋。", "align": "center"}


# ── 3. 多詞方括號標題要翻；單 token 鍵提示不翻 ────────────────────────

def test_multiword_bracket_title_translatable(tmp_path):
    raw = '{\n\tquests: [{\n\t\ttitle: "[Read Me]"\n\t\tid: "A"\n\t}]\n}\n'
    strings = read_inline_snbt_text(_write(tmp_path, raw))
    assert "[Read Me]" in strings.values()


def test_single_token_bracket_keyhints_still_skipped(tmp_path):
    raw = (
        '{\n\tquests: [{\n\t\ttitle: "[shift]"\n\t\tsubtitle: "[O]"\n'
        '\t\tid: "A"\n\t}]\n}\n'
    )
    strings = read_inline_snbt_text(_write(tmp_path, raw))
    assert strings == {}


# ── 原有行為不變 ──────────────────────────────────────────────────────

def test_cjk_and_urls_still_skipped(tmp_path):
    raw = (
        '{\n\tquests: [{\n\t\tdescription: [\n\t\t\t"已翻譯的中文行"\n'
        '\t\t\t"https://example.com/wiki"\n\t\t]\n\t\tid: "A"\n\t}]\n}\n'
    )
    strings = read_inline_snbt_text(_write(tmp_path, raw))
    assert strings == {}
