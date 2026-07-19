"""Citadel 書本 txt 內文:抽取/折行/重組單元測試。

機制(GuiBasicBook.readInPageText):全文以空格切詞、依字元數貪婪組行,
<NEWLINE> 詞 = 強制斷行+空一行;中文無空格會整段變單一長詞爆版,譯文
必須比照官方 zh_cn 慣例——每 ~16 全形寬手動斷行、行間插 <NEWLINE>。
"""
from modpack_translator.pipeline import citadel

EN_RAW = (
    "<NEWLINE>\n"
    "<NEWLINE>\n"
    "<NEWLINE>\n"
    "<NEWLINE>\n"
    "The Alligator Snapping Turtle is a massive, semi-aquatic reptile found in swamps throughout the overworld.\n"
    "Unlike its passive Sea Turtle cousins, it attacks anything that steps near its sharp, beaked mouth.\n"
    "<NEWLINE>\n"
    "If mossy, they can be sheared for a chance to drop a Spiked Scute.\n"
)

ZH_P0 = "大鱷龜是一種體型龐大的半水生爬行動物,遍布於主世界的沼澤中。與牠被動的海龜親戚不同,大鱷龜會攻擊任何靠近其尖銳喙嘴的東西。"
ZH_P1 = "如果龜殼長滿苔蘚,用剪刀剪下後有機率掉落尖刺鱗甲 (Spiked Scute)。"


def test_extract_book_txt_groups_prose_by_newline_tokens():
    got = citadel.extract_book_txt(EN_RAW)
    assert list(got) == ["p0", "p1"]
    assert got["p0"].startswith("The Alligator Snapping Turtle")
    # 實體換行 = 空格接詞(渲染器語意)
    assert "overworld. Unlike its passive" in got["p0"]
    assert got["p1"] == "If mossy, they can be sheared for a chance to drop a Spiked Scute."


def test_extract_skips_token_only_and_blank_content():
    assert citadel.extract_book_txt("<NEWLINE>\n<NEWLINE>\n") == {}
    assert citadel.extract_book_txt("") == {}
    assert citadel.extract_book_txt("---\n<NEWLINE>\n") == {}


def test_wrap_cjk_lines_width_and_content():
    lines = citadel.wrap_cjk_lines(ZH_P0)
    assert len(lines) > 1
    # 首行縮排(比照 zh_cn 官方慣例)
    assert lines[0].startswith("    ")
    for line in lines:
        assert citadel.display_width(line) <= citadel.WRAP_BUDGET + 1.0
    # 內容一字不漏(去空白後)
    joined = "".join(line.strip() for line in lines)
    assert joined.replace(" ", "") == ZH_P0.replace(" ", "")


def test_wrap_cjk_lines_keeps_ascii_words_whole():
    lines = citadel.wrap_cjk_lines(ZH_P1)
    text = "\n".join(lines)
    assert "Spiked" in text and "Scute" in text  # ASCII 詞不得被折斷
    for line in lines:
        assert citadel.display_width(line) <= citadel.WRAP_BUDGET + 3.0


def test_rebuild_replaces_prose_and_preserves_structure():
    out = citadel.rebuild_book_txt(EN_RAW, {"p0": ZH_P0, "p1": ZH_P1})
    lines = out.split("\n")
    # 開頭 4 個 <NEWLINE> 佔位(實體渲染區)原樣保留
    assert lines[0:4] == ["<NEWLINE>"] * 4
    # 譯文行間以 <NEWLINE> 分隔(強制斷行)
    assert "\n<NEWLINE>\n" in out
    assert "The Alligator Snapping Turtle" not in out
    flat = out.replace("<NEWLINE>", "").replace("\n", "").replace(" ", "")
    assert ZH_P0.replace(" ", "") in flat  # 內容一字不漏,只是被折行
    # 兩段之間的段落分隔 <NEWLINE> 保留:總數 = 前導4 + 段間1 + 折行插入
    n_wrapped_breaks = (len(citadel.wrap_cjk_lines(ZH_P0)) - 1) + (len(citadel.wrap_cjk_lines(ZH_P1)) - 1)
    assert out.count("<NEWLINE>") == 4 + 1 + n_wrapped_breaks


def test_rebuild_identity_passthrough_is_verbatim():
    src = citadel.extract_book_txt(EN_RAW)
    assert citadel.rebuild_book_txt(EN_RAW, dict(src)) == EN_RAW
    assert citadel.rebuild_book_txt(EN_RAW, {}) == EN_RAW


def test_rebuild_partial_translation_keeps_untranslated_group():
    out = citadel.rebuild_book_txt(EN_RAW, {"p1": ZH_P1})
    assert "The Alligator Snapping Turtle is a massive" in out  # p0 原樣
    assert "If mossy" not in out                                # p1 已翻
    assert "尖刺鱗甲" in out.replace("<NEWLINE>", "").replace("\n", "")
