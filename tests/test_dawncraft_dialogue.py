"""DawnCraft-Tweaks 對話折行：其自訂算繪器靠半形空格斷行（西方文字邏輯），
中文無空格會整段溢出對話框。仿官方認可的 zh_cn patch：拿掉 ¶/¬，折成每行
約 34 字、行間補空格製造斷點。因是字數邏輯，與 GUI 縮放無關。"""
import re

from modpack_translator.pipeline.preprocessor import (
    is_dawncraft_dialogue,
    rewrap_dawncraft_dialogue,
    _DIALOGUE_LINE_CHARS,
)

# 6 段、每段 10 字，以 ¶ 分頁；足以觸發折行
_SEG = "測試對話內容範例文字甲"  # 11 字
_DIALOGUE = "¶".join([_SEG * 5, _SEG * 5])  # 兩頁、各 55 字


def _visible_lines(wrapped: str) -> list[str]:
    return [ln for ln in re.split(r" {2,}", wrapped) if ln]


def test_is_dawncraft_dialogue():
    assert is_dawncraft_dialogue("Hail fellow¶...next page")
    assert not is_dawncraft_dialogue("just a normal tooltip line")


def test_strips_page_and_line_marks():
    out = rewrap_dawncraft_dialogue("前段¬中段¶後段")
    assert "¶" not in out and "¬" not in out


def test_wraps_into_short_lines():
    out = rewrap_dawncraft_dialogue(_DIALOGUE)
    lines = _visible_lines(out)
    assert len(lines) >= 2
    assert all(len(ln) <= _DIALOGUE_LINE_CHARS for ln in lines)
    # 折行處確實插入了空格串（≥2 連續空格）
    assert re.search(r" {2,}", out)


def test_idempotent():
    once = rewrap_dawncraft_dialogue(_DIALOGUE)
    twice = rewrap_dawncraft_dialogue(once)
    assert once == twice


def test_preserves_ascii_words_and_numbers():
    # 「Wiki」與「12」不可被折行切斷
    src = "收集至少 12 顆神秘之眼並前往名為 Wiki 的地方尋找更多線索與提示資訊內容" + "¶" + "補充第二頁"
    out = rewrap_dawncraft_dialogue(src)
    assert "12" in out and "Wiki" in out
    # 中間不得出現 "1 2" 或 "Wik i" 這種被拆開的情形
    assert not re.search(r"1\s+2", out)
    assert "Wik i" not in out and "Wi ki" not in out


def test_short_text_needs_no_wrap():
    out = rewrap_dawncraft_dialogue("短短一句話¶第二頁也很短")
    assert "¶" not in out
    # 兩段各很短，合併後仍可能不需折行；至少不應有多餘空格串
    # （單行以內直接回傳邏輯文字）
    assert out == "短短一句話第二頁也很短"
