from __future__ import annotations

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
    out.append((None, body))  # 本任務：正文整段保留（後續任務細分）
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
