from __future__ import annotations

import json
import re

# frontmatter：開頭 ---\n … \n---\n（容忍 CRLF 與結尾空白）
_FM_RE = re.compile(r"\A(---[ \t]*\r?\n)(.*?\r?\n)(---[ \t]*\r?\n)", re.S)
_FM_TITLE_RE = re.compile(r"^(title:[ \t]*)(.*?)([ \t]*\r?\n?)$", re.S)
_FM_CUSTOM_START_RE = re.compile(r"^custom:[ \t]*\r?\n?$")
_FM_CUSTOM_ITEM_RE = re.compile(r"^([ \t]+)([^:\r\n]+?)(:[ \t]*.*\r?\n?)$", re.S)


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


def _push_text(out: list, ctr: list[int], text: str) -> None:
    """把 text 拆成 前導空白(字面) + 核心(可翻) + 尾隨空白(字面)，保留縮排換行。"""
    if not text.strip():
        out.append((None, text))
        return
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = text[len(lead): len(text) - len(trail)] if trail else text[len(lead):]
    if lead:
        out.append((None, lead))
    out.append((_key(ctr), core))
    if trail:
        out.append((None, trail))


def _frontmatter_segments(fm: str, out: list, ctr: list[int]) -> None:
    in_custom = False
    for line in fm.splitlines(keepends=True):
        if _FM_CUSTOM_START_RE.match(line):
            in_custom = True
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


def _push_paragraph(out: list, ctr: list[int], lines: list[str]) -> None:
    if lines:
        _push_text(out, ctr, "".join(lines))


_PAIRED_JSX = {"center"}  # Callout 另行處理；其餘 4 種內僅這個是成對且無散文


def _jsx_block_end(lines: list[str], i: int) -> int:
    tag = re.match(r"^[ \t]*<([A-Za-z][A-Za-z0-9]*)", lines[i]).group(1)
    if tag in _PAIRED_JSX:
        close = f"</{tag}>"
        j = i
        while j < len(lines):
            if close in lines[j]:
                return j + 1
            j += 1
        return len(lines)
    # 自閉合（可能跨多行）：直到出現 '/>'
    j = i
    while j < len(lines):
        if "/>" in lines[j]:
            return j + 1
        j += 1
    return len(lines)


def _body_segments(body: str, out: list, ctr: list[int]) -> None:
    lines = body.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            out.append((None, line)); i += 1; continue
        if _JSX_LINE_RE.match(line):
            if re.match(r"^[ \t]*<Callout\b", line):
                out.append((None, line)); i += 1
                inner: list[str] = []
                while i < len(lines) and not re.match(r"^[ \t]*</Callout>", lines[i]):
                    inner.append(lines[i]); i += 1
                _push_paragraph(out, ctr, inner)
                if i < len(lines):
                    out.append((None, lines[i])); i += 1
                continue
            if _JSX_OPEN_RE.match(line):
                j = _jsx_block_end(lines, i)
                for k in range(i, j):
                    out.append((None, lines[k]))
                i = j
                continue
            out.append((None, line)); i += 1; continue   # 散雜 < 行（如孤立閉標籤/註解）原樣保留
        mh = _HEADING_RE.match(line)
        if mh:
            out.append((None, mh.group(1))); _push_text(out, ctr, mh.group(2)); i += 1; continue
        ml = _LIST_RE.match(line)
        if ml:
            out.append((None, ml.group(1))); _push_text(out, ctr, ml.group(2)); i += 1; continue
        para: list[str] = []
        while i < len(lines):
            l = lines[i]
            if (not l.strip()) or _JSX_LINE_RE.match(l) or _HEADING_RE.match(l) or _LIST_RE.match(l):
                break
            para.append(l); i += 1
        _push_paragraph(out, ctr, para)
