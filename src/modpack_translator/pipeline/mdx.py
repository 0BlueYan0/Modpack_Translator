from __future__ import annotations

import json
import re

# frontmatter：開頭 ---\n … \n---\n（容忍 CRLF 與結尾空白）
_FM_RE = re.compile(r"\A(---[ \t]*\r?\n)(.*?\r?\n)(---[ \t]*\r?\n)", re.S)
_FM_TITLE_RE = re.compile(r"^(title:[ \t]*)(.*?)([ \t]*\r?\n?)$", re.S)
_FM_CUSTOM_START_RE = re.compile(r"^custom:[ \t]*\r?\n?$")
_FM_CUSTOM_ITEM_RE = re.compile(r"^([ \t]+)([^:\r\n]+?)(:[ \t]*.*\r?\n?)$", re.S)
# GuideME frontmatter：navigation: 區塊的 title 子鍵（側欄/目錄顯示名）。
# parent/icon/position/icon_components 與頂層 categories/item_ids 為結構,一律保留。
_FM_NAV_START_RE = re.compile(r"^navigation:[ \t]*\r?\n?$")
_FM_NAV_TITLE_Q_RE = re.compile(r"^([ \t]+title:[ \t]*)([\"'])(.*)(\2)([ \t]*\r?\n?)$", re.S)
_FM_NAV_TITLE_P_RE = re.compile(r"^([ \t]+title:[ \t]*)(.*?)([ \t]*\r?\n?)$", re.S)


def extract_mdx(raw: str) -> dict[str, str]:
    return {k: t for k, t in _segments(raw) if k is not None}


def rebuild_mdx(raw: str, translations: dict[str, str]) -> str:
    return "".join(
        (translations.get(k, t) if k is not None else t)
        for k, t in _segments(raw)
    )


def extract_meta(raw: str) -> dict[str, str]:
    """Oracle _meta.json → 可翻標題 dict。值為字串取其身;值為 {"name": str,...} 取 name。其餘(icon/null)不列入。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, str):
            out[k] = v
        elif isinstance(v, dict) and isinstance(v.get("name"), str):
            out[k] = v["name"]
    return out


def rebuild_meta(raw: str, translations: dict[str, str]) -> str:
    """把譯文填回 _meta.json,保留鍵順序與 dict 值的其他欄位(icon 等),不丟任何鍵。"""
    data = json.loads(raw)
    for k, v in data.items():
        if k not in translations:
            continue
        if isinstance(v, str):
            data[k] = translations[k]
        elif isinstance(v, dict) and isinstance(v.get("name"), str):
            v["name"] = translations[k]
    return json.dumps(data, ensure_ascii=False, indent=2)


def _segments(raw: str) -> list[tuple[str | None, str]]:
    out: list[tuple[str | None, str]] = []
    ctr = [0]
    m = _FM_RE.match(raw)
    if m:
        out.append((None, m.group(1)))
        _frontmatter_segments(m.group(2), out, ctr)
        out.append((None, m.group(3)))
        body = raw[m.end():]
    else:
        body = raw
    _body_segments(body, out, ctr)
    return out


def _key(ctr: list[int]) -> str:
    k = f"s{ctr[0]}"
    ctr[0] += 1
    return k


# 行內 markdown 圖片：![alt](src)。純圖片/純標籤段(去除後無實質字元)不送翻:
# 顯示文字不在 markdown 裡(ItemLink 名稱來自 lang 檔),模型原樣返回會被
# 輸出關卡拒收,形成「永遠待翻」殘留。
_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_TAG_SPAN_RE = re.compile(r"<[^<>]*>")


def _has_translatable_char(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _push_text(out: list, ctr: list[int], text: str) -> None:
    """把 text 拆成 前導空白(字面) + 核心(可翻) + 尾隨空白(字面)，保留縮排換行。
    核心去除圖片構造與完整標籤後無字母/數字(純圖片、純標籤、純符號)→ 整段字面保留。"""
    if not text.strip():
        out.append((None, text))
        return
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = text[len(lead): len(text) - len(trail)] if trail else text[len(lead):]
    if not _has_translatable_char(_TAG_SPAN_RE.sub("", _IMG_MD_RE.sub("", core))):
        out.append((None, text))
        return
    if lead:
        out.append((None, lead))
    out.append((_key(ctr), core))
    if trail:
        out.append((None, trail))


def _frontmatter_segments(fm: str, out: list, ctr: list[int]) -> None:
    in_custom = False
    in_nav = False
    nav_indent: int | None = None
    for line in fm.splitlines(keepends=True):
        if _FM_CUSTOM_START_RE.match(line):
            in_custom = True
            in_nav = False
            out.append((None, line))
            continue
        if _FM_NAV_START_RE.match(line):
            in_nav = True
            in_custom = False
            nav_indent = None
            out.append((None, line))
            continue
        if in_custom:
            if line[:1] in " \t":
                m = _FM_CUSTOM_ITEM_RE.match(line)
                if m:
                    out.append((None, m.group(1)))       # 縮排
                    _push_text(out, ctr, m.group(2))       # 標籤（可翻）
                    out.append((None, m.group(3)))         # ": 值\n"
                    continue
            else:
                in_custom = False  # 退縮排 → custom 區塊結束，往下判定
        if in_nav:
            if line[:1] in " \t":
                indent = len(line) - len(line.lstrip(" \t"))
                if nav_indent is None:
                    nav_indent = indent  # 首個子鍵定錨:更深層(如 icon_components 內)不判 title
                if indent == nav_indent:
                    mq = _FM_NAV_TITLE_Q_RE.match(line)
                    if mq:
                        out.append((None, mq.group(1) + mq.group(2)))  # '  title: "'
                        _push_text(out, ctr, mq.group(3))               # 引號內文（可翻）
                        out.append((None, mq.group(4) + mq.group(5)))  # '"' + 尾隨
                        continue
                    mp = _FM_NAV_TITLE_P_RE.match(line)
                    if mp and mp.group(2):
                        out.append((None, mp.group(1)))                 # '  title: '
                        _push_text(out, ctr, mp.group(2))               # 值（可翻）
                        out.append((None, mp.group(3)))                 # 尾隨
                        continue
                out.append((None, line))                                # parent/icon/position…
                continue
            else:
                in_nav = False  # 退縮排 → navigation 區塊結束，往下判定
        mt = _FM_TITLE_RE.match(line)
        if mt and not line[:1].isspace():
            out.append((None, mt.group(1)))               # "title: "
            _push_text(out, ctr, mt.group(2))              # 值（可翻）
            out.append((None, mt.group(3)))               # 尾隨
            continue
        out.append((None, line))                          # id/type/related_items/custom 值…


_HEADING_RE = re.compile(r"^([ \t]*#{1,6}[ \t]+)(.*)$", re.S)
_LIST_RE = re.compile(r"^([ \t]*(?:[-*+]|\d+\.)[ \t]+)(.*)$", re.S)
_JSX_OPEN_RE = re.compile(r"^[ \t]*<[A-Za-z][A-Za-z0-9]*")
_JSX_LINE_RE = re.compile(r"^[ \t]*<")
# 圍欄程式碼區塊：``` 或 ~~~（3 個以上）。區塊內是程式碼,原樣保留不送翻。
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
# 表格：| 起始的連續行。逐列切段(表頭可翻),分隔列(|---|:---:|)字面保留。
_TABLE_ROW_RE = re.compile(r"^[ \t]*\|")
_TABLE_SEP_RE = re.compile(r"^[ \t]*\|[ \t:\-|]*\r?\n?$")
_TAG_NAME_RE = re.compile(r"^[ \t]*<([A-Za-z][A-Za-z0-9]*)")


def _is_close_fence(line: str, fence_char: str, fence_len: int) -> bool:
    """收尾圍欄：整行只由相同圍欄字元組成且長度 >= 開頭（開頭那行可帶語言標
    示如 ```groovy,收尾行不得有其他內容)。"""
    stripped = line.strip()
    return len(stripped) >= fence_len and set(stripped) == {fence_char}


def _push_paragraph(out: list, ctr: list[int], lines: list[str]) -> None:
    if lines:
        _push_text(out, ctr, "".join(lines))


def _is_inline_prose_start(line: str) -> bool:
    """行首為標籤,但行內所有標籤同行完結、去標籤後仍有實質文字
    → 是段落散文(如 <Color …>south</Color>, … 的段落續行),不是 JSX 塊。"""
    if not _JSX_OPEN_RE.match(line):
        return False
    residue = _TAG_SPAN_RE.sub("", line)
    if "<" in residue:
        return False  # 有未完結(跨行)標籤 → 交給 JSX 塊處理
    return _has_translatable_char(_IMG_MD_RE.sub("", residue))


def _para_break(line: str) -> bool:
    if not line.strip():
        return True
    if _FENCE_RE.match(line) or _TABLE_ROW_RE.match(line):
        return True
    if _JSX_LINE_RE.match(line) and not _is_inline_prose_start(line):
        return True
    return bool(_HEADING_RE.match(line) or _LIST_RE.match(line))


def _consume_jsx(lines: list[str], i: int, out: list, ctr: list[int]) -> int:
    """JSX 元件塊。自閉合(可跨多行至 '/>')整段保留;容器(<Tag …>)保留開閉標籤行、
    內部行遞迴切段(GuideME 的 Row/Column/div 等容器內是一般 markdown);
    找不到同名閉標籤 → 視為立即自閉合(容忍手寫未閉合頁,不吞後續散文)。
    回傳下一行索引。"""
    n = len(lines)
    j = i
    text = lines[i]
    while ">" not in text and j + 1 < n:
        j += 1
        text += lines[j]
    gt = text.find(">")
    if gt == -1:  # 到檔尾都沒有 '>':整段原樣保留
        for k in range(i, n):
            out.append((None, lines[k]))
        return n
    if text[:gt].rstrip().endswith("/"):  # 自閉合（可能跨多行）
        for k in range(i, j + 1):
            out.append((None, lines[k]))
        return j + 1
    tag = _TAG_NAME_RE.match(lines[i]).group(1)
    close_re = re.compile(rf"^[ \t]*</{tag}>[ \t]*\r?\n?$")
    open_again_re = re.compile(rf"^[ \t]*<{tag}\b")
    close_token = f"</{tag}>"
    depth = 1
    close_at: int | None = None
    k = j + 1
    while k < n:
        cur = lines[k]
        if close_re.match(cur):
            depth -= 1
            if depth == 0:
                close_at = k
                break
        elif open_again_re.match(cur) and "/>" not in cur and close_token not in cur:
            depth += 1  # 同名巢狀
        k += 1
    for m in range(i, j + 1):
        out.append((None, lines[m]))  # 開標籤行（含跨行屬性）保留
    if close_at is None:
        return j + 1  # 未閉合 → 只保留開標籤,後續行照常切段
    _segment_lines(lines[j + 1: close_at], out, ctr)  # 容器內部遞迴
    out.append((None, lines[close_at]))               # 閉標籤行保留
    return close_at + 1


def _body_segments(body: str, out: list, ctr: list[int]) -> None:
    _segment_lines(body.splitlines(keepends=True), out, ctr)


def _segment_lines(lines: list[str], out: list, ctr: list[int]) -> None:
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            out.append((None, line)); i += 1; continue
        mf = _FENCE_RE.match(line)
        if mf:
            fence_char, fence_len = mf.group(1)[0], len(mf.group(1))
            out.append((None, line)); i += 1          # 開頭圍欄
            while i < n:
                cur = lines[i]
                out.append((None, cur)); i += 1        # 程式碼內容 + 收尾圍欄原樣保留
                if _is_close_fence(cur, fence_char, fence_len):
                    break
            continue
        if _TABLE_ROW_RE.match(line):
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                row = lines[i]
                if _TABLE_SEP_RE.match(row):
                    out.append((None, row))
                else:
                    _push_text(out, ctr, row)
                i += 1
            continue
        if _JSX_LINE_RE.match(line) and not _is_inline_prose_start(line):
            if re.match(r"^[ \t]*<Callout\b", line):
                out.append((None, line)); i += 1
                inner: list[str] = []
                while i < n and not re.match(r"^[ \t]*</Callout>", lines[i]):
                    inner.append(lines[i]); i += 1
                _push_paragraph(out, ctr, inner)
                if i < n:
                    out.append((None, lines[i])); i += 1
                continue
            if _JSX_OPEN_RE.match(line):
                i = _consume_jsx(lines, i, out, ctr)
                continue
            out.append((None, line)); i += 1; continue   # 散雜 < 行（如孤立閉標籤/註解）原樣保留
        mh = _HEADING_RE.match(line)
        if mh:
            out.append((None, mh.group(1))); _push_text(out, ctr, mh.group(2)); i += 1; continue
        ml = _LIST_RE.match(line)
        if ml:
            out.append((None, ml.group(1))); _push_text(out, ctr, ml.group(2)); i += 1; continue
        para: list[str] = []
        while i < n and not _para_break(lines[i]):
            para.append(lines[i]); i += 1
        _push_paragraph(out, ctr, para)
