"""Citadel 書本系統(Alex's Mobs 動物圖鑑、Alex's Caves 洞穴書、Citadel 自帶書)。

機制(GuiBasicBook):頁面 JSON 的 "text" 指向 txt 內文;檔案路徑為
<textFileDirectory>/<遊戲語言小寫>/<相對路徑>,開檔失敗才逐檔 fallback en_us/
——所以只要提供 zh_tw/ 鏡像樹,遊戲原生載入。

渲染(readInPageText):全文以「空格」切詞(實體換行等同空格),依字元數
(左欄 35/右欄 30)貪婪組行;字面 token <NEWLINE> = 強制斷行再空一行。
中文無空格,整段會被當成單一長詞塞進一行爆版——官方 zh_cn 譯檔的解法是
每行手動斷在 ~16 全形寬、行間插 <NEWLINE>(雙行距是官方接受的取捨),
譯文比照同一慣例輸出。
"""
from __future__ import annotations

import re
import unicodedata

NEWLINE_TOKEN = "<NEWLINE>"
# 每行顯示寬度預算(全形=1.0、半形=0.5):zh_cn 官方譯檔實測為 15-17 全形
WRAP_BUDGET = 16.0
_PARAGRAPH_INDENT = "    "  # zh_cn 官方慣例:段首 4 個半形空格縮排

_HAS_ALNUM_RE = re.compile(r"[^\W_]", re.UNICODE)
_CJK_RE = re.compile(r"[㐀-鿿]")


def _is_token_line(line: str) -> bool:
    return line.strip() == NEWLINE_TOKEN


def _iter_groups(raw: str) -> list[tuple[bool, list[str]]]:
    """把內文切成 (is_prose, lines) 群組:<NEWLINE> 行(含空行)是版面結構,
    連續的一般文字行是一個散文段(渲染器把實體換行當空格接詞)。"""
    groups: list[tuple[bool, list[str]]] = []
    for line in raw.splitlines():
        is_prose = bool(line.strip()) and not _is_token_line(line)
        if groups and groups[-1][0] == is_prose:
            groups[-1][1].append(line)
        else:
            groups.append((is_prose, [line]))
    return groups


def _prose_value(lines: list[str]) -> str:
    return " ".join(part for part in (line.strip() for line in lines) if part)


def extract_book_txt(raw: str) -> dict[str, str]:
    """散文段 → {p0: 內文, p1: …}。無字母數字的段(純符號)不列入。"""
    out: dict[str, str] = {}
    idx = 0
    for is_prose, lines in _iter_groups(raw):
        if not is_prose:
            continue
        value = _prose_value(lines)
        if _HAS_ALNUM_RE.search(value):
            out[f"p{idx}"] = value
        idx += 1
    return out


def rebuild_book_txt(raw: str, translations: dict[str, str]) -> str:
    """把譯文填回:<NEWLINE>/空行結構原樣保留;有譯文且非原樣的散文段
    改寫為 CJK 折行(行間插 <NEWLINE>),其餘段落逐行原樣。"""
    out_lines: list[str] = []
    idx = 0
    for is_prose, lines in _iter_groups(raw):
        if not is_prose:
            out_lines.extend(lines)
            continue
        value = _prose_value(lines)
        key = f"p{idx}"
        idx += 1
        translated = translations.get(key) if _HAS_ALNUM_RE.search(value) else None
        if translated is None or translated == value:
            out_lines.extend(lines)
            continue
        wrapped = wrap_cjk_lines(translated)
        for i, wline in enumerate(wrapped):
            if i:
                out_lines.append(NEWLINE_TOKEN)
            out_lines.append(wline)
    text = "\n".join(out_lines)
    if raw.endswith(("\n", "\r\n")):
        text += "\n"
    return text


def display_width(text: str) -> float:
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.5 for ch in text)


def _wrap_units(text: str) -> list[str]:
    """折行單位:空白、單一全形字(逐字可斷)、連續半形串(ASCII 詞保持完整)。"""
    units: list[str] = []
    word = ""
    for ch in text:
        if ch.isspace() or unicodedata.east_asian_width(ch) in ("W", "F"):
            if word:
                units.append(word)
                word = ""
            units.append(" " if ch.isspace() else ch)
        else:
            word += ch
    if word:
        units.append(word)
    return units


def wrap_cjk_lines(text: str, budget: float = WRAP_BUDGET) -> list[str]:
    """依顯示寬度貪婪折行:CJK 逐字可斷,ASCII 詞保持完整;段首縮排
    (比照 zh_cn 官方譯檔)。回傳行清單(不含行尾換行)。"""
    lines: list[str] = []
    cur = _PARAGRAPH_INDENT
    cur_w = display_width(_PARAGRAPH_INDENT)
    pending_space = ""
    for unit in _wrap_units(text):
        if unit.isspace():
            pending_space = " "
            continue
        unit_w = display_width(unit)
        sep = pending_space if cur.strip() else ""
        if cur.strip() and cur_w + display_width(sep) + unit_w > budget:
            lines.append(cur)
            cur, cur_w, sep = "", 0.0, ""
        cur += sep + unit
        cur_w += display_width(sep) + unit_w
        pending_space = ""
    if cur.strip():
        lines.append(cur)
    return lines or [text]


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))
