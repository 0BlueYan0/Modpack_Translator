# Oracle Wiki(MDX)翻譯支援 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓翻譯工具把 Oracle 指南(oracle_index）的 MDX 內文與 `_meta.json` 自動翻成繁中,輸出到 viewer 讀得到的 `translated/zh_tw/<root>/`。

**Architecture:** 新增 `pipeline/mdx.py` 做「區塊層」切段(exact-cover:所有片段接回即原文),把可翻文字抽成有序 dict,沿用既有 `translate_dict`(glossary/快取/驗證/回退)翻譯後重建 MDX;`_meta.json` 直接復用既有 `json_lang` 格式。掃描與寫入沿用既有「寫正規小寫目標 + 讀既有重用」架構(jar_inject),交付到 `assets/oracle_index/books/<book>/translated/zh_tw/<root>/<相對路徑>`。

**Tech Stack:** Python 3.12、stdlib `re`/`zipfile`、pytest。無新增第三方依賴。

## Global Constraints

- 語言碼一律小寫 `zh_tw`(遊戲設定與範本 `fr_fr` 慣例)。
- 不引入 markdown 函式庫;切段用 stdlib `re`,沿用 `preprocessor.encode/decode` 的行內保護。
- 行尾為 CRLF;切段必須 **exact-cover**(所有片段文字接回等於原文,逐位元組相同)。
- 結構 token 沒保住的段落回退英文(沿用 `is_usable_translation`),絕不弄壞渲染。
- 只處理 jar 內建 MDX;不抓線上 wiki;不加 GUI 開關(自動,與其他格式一致)。
- 測試用專案 venv:`.venv/Scripts/python.exe -m pytest`。

## File Structure

- **Create** `src/modpack_translator/pipeline/mdx.py` — MDX 切段/抽取/重建(`extract_mdx`、`rebuild_mdx` 及內部 `_segments`)。單一職責:MDX↔可翻字串 dict。
- **Create** `tests/test_mdx.py` — mdx.py 單元測試。
- **Create** `tests/test_oracle_wiki_scan.py` — scanner 對 Oracle 書的偵測測試。
- **Modify** `src/modpack_translator/pipeline/patcher.py` — 新增 `write_jar_text`。
- **Modify** `src/modpack_translator/pipeline/scanner.py` — `_scan_jar` 內新增 Oracle 書偵測,產生 `oracle_mdx`(mdx)與 `json_lang`(_meta.json)target。
- **Modify** `src/modpack_translator/pipeline/runner.py` — `process_target`/`read_existing_target`/`read_target_strings` 新增 `oracle_mdx` 分支。
- **Modify** 版本號三處 + 同步 Downloads 執行版。

## 背景資料(實作前必讀)

- 來源路徑:`assets/oracle_index/books/<book>/<root>/**`,`<root> ∈ {content, docs}`。`.mdx` 是文章,`_meta.json` 是導覽標題 `{檔名或目錄: 標題}`。
- 交付路徑:把 `<root>` 之前插入 `translated/zh_tw/`,即 `assets/oracle_index/books/<book>/translated/zh_tw/<root>/<相對路徑>`。已由 oritech 內建 `translated/fr_fr/content/…` 證實。
- MDX 語法(全 152 篇普查):frontmatter 鍵僅 `id/type/title/custom/related_items`;JSX 僅 4 種——`<Callout variant="…">…</Callout>`(成對,內含散文)、`<center>…</center>`(成對,只包 `<ModAsset/>`)、`<ModAsset …/>`(自閉合)、`<CraftingRecipe …/>`(自閉合、可跨多行);連結 `[文字](@ns:ref)` 或 `[文字](https://…)`;`**粗體**`;標題 `#`–`####`;清單 `- `;**無表格、無 code fence**;行尾 CRLF。
- 翻譯規則:frontmatter 只翻 `title` 值與 `custom` 標籤鍵,保留 `id/type/related_items` 與 `custom` 值;正文翻散文/標題/清單/Callout 內文;連結只翻文字(`@ns:ref`/URL 由既有 `encode()` 保護);`<center>/<ModAsset/>/<CraftingRecipe/>` 整段保留。

---

### Task 1: mdx.py — frontmatter 切段(title + custom 標籤)

**Files:**
- Create: `src/modpack_translator/pipeline/mdx.py`
- Test: `tests/test_mdx.py`

**Interfaces:**
- Produces: `extract_mdx(raw: str) -> dict[str, str]`、`rebuild_mdx(raw: str, translations: dict[str, str]) -> str`、內部 `_segments(raw) -> list[tuple[str|None, str]]`(key=None 為保留字面;key=str 為可翻;所有片段文字接回等於 raw)。本任務先只處理 frontmatter,正文整段保留。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_mdx.py
from modpack_translator.pipeline.mdx import extract_mdx, rebuild_mdx

FM = (
    "---\r\n"
    "id: oritech:chainsaw\r\n"
    "title: Chainsaw\r\n"
    "type: item\r\n"
    "custom:\r\n"
    "    RF Capacity: \"10,000\"\r\n"
    "    Charge speed: \"512 RF/t\"\r\n"
    "related_items: [\"oritech:charger_block\"]\r\n"
    "---\r\n"
    "\r\n"
    "Body stays literal for now.\r\n"
)

def test_frontmatter_extracts_title_and_custom_labels():
    got = extract_mdx(FM)
    assert set(got.values()) >= {"Chainsaw", "RF Capacity", "Charge speed"}
    # 保留項不可被抽出
    assert "oritech:chainsaw" not in got.values()
    assert "item" not in got.values()

def test_frontmatter_rebuild_is_exact_when_no_translation():
    assert rebuild_mdx(FM, {}) == FM

def test_frontmatter_rebuild_applies_translation():
    got = extract_mdx(FM)
    title_key = next(k for k, v in got.items() if v == "Chainsaw")
    cap_key = next(k for k, v in got.items() if v == "RF Capacity")
    out = rebuild_mdx(FM, {title_key: "鏈鋸", cap_key: "RF 容量"})
    assert "title: 鏈鋸\r\n" in out
    assert "    RF 容量: \"10,000\"\r\n" in out
    assert "id: oritech:chainsaw\r\n" in out          # 保留
    assert "type: item\r\n" in out                     # 保留
    assert "    Charge speed: \"512 RF/t\"\r\n" in out  # 未譯者原樣
```

- [ ] **Step 2: 執行確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mdx.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'modpack_translator.pipeline.mdx'`）

- [ ] **Step 3: 實作最小程式碼**

```python
# src/modpack_translator/pipeline/mdx.py
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
```

- [ ] **Step 4: 執行確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mdx.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/mdx.py tests/test_mdx.py
git commit -m "feat(mdx): frontmatter 切段(title + custom 標籤)"
```

---

### Task 2: mdx.py — 正文散文區塊(標題／清單／段落)

**Files:**
- Modify: `src/modpack_translator/pipeline/mdx.py`
- Test: `tests/test_mdx.py`

**Interfaces:**
- Consumes: Task 1 的 `_segments`、`_push_text`、`_key`。
- Produces: 新增 `_body_segments(body, out, ctr)`、`_push_paragraph(...)`,並把 `_segments` 中「正文整段保留」改為呼叫 `_body_segments`。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_mdx.py（追加）
BODY = (
    "---\r\ntitle: X\r\ntype: item\r\nid: m:x\r\n---\r\n"
    "\r\n"
    "The chainsaw is a fast tool. It also\r\n"
    "works as a sword.\r\n"
    "\r\n"
    "### How to use\r\n"
    "\r\n"
    "- Charge it in a [charger](@oritech:charger_block).\r\n"
    "- Hold **Shift** to fell trees.\r\n"
)

def test_body_prose_blocks_extracted():
    vals = list(extract_mdx(BODY).values())
    assert "The chainsaw is a fast tool. It also\r\nworks as a sword." in vals  # 段落含軟換行
    assert "How to use" in vals                                                # 標題文字
    assert "Charge it in a [charger](@oritech:charger_block)." in vals          # 清單項含連結原文
    assert "Hold **Shift** to fell trees." in vals

def test_body_markers_preserved_on_rebuild():
    got = extract_mdx(BODY)
    hk = next(k for k, v in got.items() if v == "How to use")
    out = rebuild_mdx(BODY, {hk: "如何使用"})
    assert "### 如何使用\r\n" in out       # 保留 "### " 與換行
    assert "- Charge it in a [charger]" in out  # 清單標記保留

def test_body_rebuild_exact_when_no_translation():
    assert rebuild_mdx(BODY, {}) == BODY
```

- [ ] **Step 2: 執行確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mdx.py -k body -v`
Expected: FAIL（段落/標題未被抽出——目前正文整段保留）

- [ ] **Step 3: 實作最小程式碼**

把 `_segments` 內 `out.append((None, body))` 改為 `_body_segments(body, out, ctr)`,並新增：

```python
# src/modpack_translator/pipeline/mdx.py（追加）
_HEADING_RE = re.compile(r"^([ \t]*#{1,6}[ \t]+)(.*)$", re.S)
_LIST_RE = re.compile(r"^([ \t]*(?:[-*+]|\d+\.)[ \t]+)(.*)$", re.S)
_JSX_OPEN_RE = re.compile(r"^[ \t]*<[A-Za-z][A-Za-z0-9]*")


def _push_paragraph(out: list, ctr: list[int], lines: list[str]) -> None:
    if lines:
        _push_text(out, ctr, "".join(lines))


def _body_segments(body: str, out: list, ctr: list[int]) -> None:
    lines = body.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            out.append((None, line)); i += 1; continue
        if _JSX_OPEN_RE.match(line):
            out.append((None, line)); i += 1; continue   # 本任務：JSX 行先原樣保留（Task 3 細分）
        mh = _HEADING_RE.match(line)
        if mh:
            out.append((None, mh.group(1))); _push_text(out, ctr, mh.group(2)); i += 1; continue
        ml = _LIST_RE.match(line)
        if ml:
            out.append((None, ml.group(1))); _push_text(out, ctr, ml.group(2)); i += 1; continue
        para: list[str] = []
        while i < len(lines):
            l = lines[i]
            if (not l.strip()) or _JSX_OPEN_RE.match(l) or _HEADING_RE.match(l) or _LIST_RE.match(l):
                break
            para.append(l); i += 1
        _push_paragraph(out, ctr, para)
```

- [ ] **Step 4: 執行確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mdx.py -v`
Expected: PASS（Task 1 + Task 2 全部）

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/mdx.py tests/test_mdx.py
git commit -m "feat(mdx): 正文標題/清單/段落切段"
```

---

### Task 3: mdx.py — JSX 區塊(Callout 內文翻、其餘保留)

**Files:**
- Modify: `src/modpack_translator/pipeline/mdx.py`
- Test: `tests/test_mdx.py`

**Interfaces:**
- Consumes: Task 2 的 `_body_segments`、`_push_paragraph`。
- Produces: 新增 `_jsx_block_end(lines, i) -> int`;`_body_segments` 的 JSX 分支改為:Callout 翻內文、其餘(center/ModAsset/CraftingRecipe)整段保留。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_mdx.py（追加）
JSX = (
    "---\r\ntitle: X\r\ntype: item\r\nid: m:x\r\n---\r\n"
    "\r\n"
    "<Callout variant=\"info\">\r\n"
    "    The forests will fall.\r\n"
    "</Callout>\r\n"
    "\r\n"
    "<center>\r\n"
    "<ModAsset location=\"oritech:area/x\" width={512} />\r\n"
    "</center>\r\n"
    "\r\n"
    "<CraftingRecipe\r\n"
    "    slots={[\r\n"
    "        '', 'oritech:steel_ingot', '',\r\n"
    "    ]}\r\n"
    "/>\r\n"
)

def test_callout_inner_text_is_translatable():
    assert "The forests will fall." in extract_mdx(JSX).values()

def test_jsx_structure_preserved_and_only_callout_translated():
    got = extract_mdx(JSX)
    # ModAsset/CraftingRecipe/center/slots 內容不可被抽成可翻
    assert not any("ModAsset" in v or "slots" in v or "steel_ingot" in v for v in got.values())
    ck = next(k for k, v in got.items() if v == "The forests will fall.")
    out = rebuild_mdx(JSX, {ck: "森林將傾倒。"})
    assert "<Callout variant=\"info\">\r\n" in out          # 開標籤保留
    assert "</Callout>\r\n" in out                          # 閉標籤保留
    assert "    森林將傾倒。\r\n" in out                     # 內文翻譯、縮排保留
    assert "<ModAsset location=\"oritech:area/x\" width={512} />\r\n" in out
    assert "        '', 'oritech:steel_ingot', '',\r\n" in out  # CraftingRecipe 多行整段保留

def test_jsx_rebuild_exact_when_no_translation():
    assert rebuild_mdx(JSX, {}) == JSX
```

- [ ] **Step 2: 執行確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mdx.py -k jsx_or_callout -v` 或 `-k "callout or jsx"`
Expected: FAIL（Callout 內文未被抽出;CraftingRecipe 多行未正確保留為區塊）

- [ ] **Step 3: 實作最小程式碼**

在 `_body_segments` 把 `if _JSX_OPEN_RE.match(line):` 分支替換為:

```python
        if _JSX_OPEN_RE.match(line):
            if re.match(r"^[ \t]*<Callout\b", line):
                out.append((None, line)); i += 1
                inner: list[str] = []
                while i < len(lines) and not re.match(r"^[ \t]*</Callout>", lines[i]):
                    inner.append(lines[i]); i += 1
                _push_paragraph(out, ctr, inner)
                if i < len(lines):
                    out.append((None, lines[i])); i += 1
                continue
            j = _jsx_block_end(lines, i)
            for k in range(i, j):
                out.append((None, lines[k]))
            i = j
            continue
```

並新增:

```python
_PAIRED_JSX = {"center"}  # Callout 另行處理;其餘 4 種內僅這個是成對且無散文


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
```

- [ ] **Step 4: 執行確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mdx.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/mdx.py tests/test_mdx.py
git commit -m "feat(mdx): JSX 區塊(Callout 翻內文、center/ModAsset/CraftingRecipe 保留)"
```

---

### Task 4: mdx.py — 真實檔 round-trip 保證

**Files:**
- Modify: `tests/test_mdx.py`

**Interfaces:**
- Consumes: `extract_mdx`、`rebuild_mdx`。
- Produces: 無新程式碼;釘住「identity 重建 == 原文」與「連結目標保留」不變式。

- [ ] **Step 1: 寫失敗測試(先當回歸鎖,可能一次就過)**

```python
# tests/test_mdx.py（追加）
REAL = (  # 濃縮自 oritech content/equipment/chainsaw.mdx 的代表結構
    "---\r\n"
    "id: oritech:chainsaw\r\n"
    "title: Chainsaw\r\n"
    "type: item\r\n"
    "custom:\r\n"
    "    RF Capacity: \"10,000\"\r\n"
    "---\r\n"
    "\r\n"
    "The chainsaw is a fast tool for harvesting wood. It functions as an axe\r\n"
    "that never breaks.\r\n"
    "\r\n"
    "<Callout variant=\"info\">\r\n"
    "    The forests will fall.\r\n"
    "</Callout>\r\n"
    "\r\n"
    "### How to use\r\n"
    "\r\n"
    "Charge the chainsaw in a [charger](@oritech:charger_block).\r\n"
)

def test_identity_rebuild_reproduces_source_byte_for_byte():
    assert rebuild_mdx(REAL, {}) == REAL

def test_link_target_preserved_when_link_text_translated():
    got = extract_mdx(REAL)
    k = next(key for key, v in got.items() if v.startswith("Charge the chainsaw"))
    out = rebuild_mdx(REAL, {k: "在[充電器](@oritech:charger_block)中充電。"})
    assert "(@oritech:charger_block)" in out

def test_all_extracted_values_are_nonempty_prose():
    for v in extract_mdx(REAL).values():
        assert v.strip()                     # 無空白段
        assert not v.lstrip().startswith("<")  # 未把 JSX 標籤當可翻
```

- [ ] **Step 2: 執行**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mdx.py -v`
Expected: PASS（若失敗代表 Task 1–3 有 exact-cover 破綻,修 mdx.py 直到通過）

- [ ] **Step 3: Commit**

```bash
git add tests/test_mdx.py
git commit -m "test(mdx): 真實檔 round-trip 與連結目標保留不變式"
```

---

### Task 5: patcher.py — write_jar_text

**Files:**
- Modify: `src/modpack_translator/pipeline/patcher.py`
- Test: `tests/test_patcher_write_text.py`（Create）

**Interfaces:**
- Consumes: 既有 `_rewrite_jar`。
- Produces: `write_jar_text(jar_path: Path, path_in_jar: str, text: str) -> None`（UTF-8 寫入 jar 內任意文字檔）。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_patcher_write_text.py
import zipfile
from pathlib import Path
from modpack_translator.pipeline.patcher import write_jar_text

def test_write_jar_text_adds_utf8_entry(tmp_path):
    jar = tmp_path / "m.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/x/keep.txt", "keep")
    write_jar_text(jar, "assets/x/books/b/translated/zh_tw/content/a.mdx", "標題\r\n內文")
    with zipfile.ZipFile(jar) as zf:
        names = set(zf.namelist())
        assert "assets/x/books/b/translated/zh_tw/content/a.mdx" in names
        assert "assets/x/keep.txt" in names  # 原內容保留
        assert zf.read("assets/x/books/b/translated/zh_tw/content/a.mdx").decode("utf-8") == "標題\r\n內文"
```

- [ ] **Step 2: 執行確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_patcher_write_text.py -v`
Expected: FAIL（`ImportError: cannot import name 'write_jar_text'`）

- [ ] **Step 3: 實作最小程式碼**

在 `patcher.py` `write_jar_json_file` 之後新增:

```python
def write_jar_text(jar_path: Path, path_in_jar: str, text: str) -> None:
    _rewrite_jar(jar_path, {path_in_jar: text.encode("utf-8")})
```

- [ ] **Step 4: 執行確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_patcher_write_text.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/patcher.py tests/test_patcher_write_text.py
git commit -m "feat(patcher): 新增 write_jar_text 寫入 jar 內文字檔"
```

---

### Task 6: scanner.py — 偵測 Oracle 書(mdx + _meta.json)

**Files:**
- Modify: `src/modpack_translator/pipeline/scanner.py`
- Test: `tests/test_oracle_wiki_scan.py`（Create）

**Interfaces:**
- Consumes: 既有 `TranslationTarget`、`_jar_lang_needs_translation`、`preprocessor.parse_json_lang`、`diff_keys`;Task 1 的 `mdx.extract_mdx`。
- Produces: `_scan_jar` 迴圈內對符合 `assets/oracle_index/books/<book>/<root>/…`(`root∈{content,docs}`、路徑不含 `translated`)的檔產生 target:
  - `.mdx` → `format="oracle_mdx"`;`_meta.json` → `format="json_lang"`。
  - 兩者 `output_mode="jar_inject"`、`mod_id=<book>`、`target_path_in_jar`/`existing_path_in_jar`＝在 `<root>` 前插入 `translated/zh_tw/`(existing 不存在則 None)。
- 需翻判定:`.mdx` 用 `extract_mdx` 後 `diff_keys` 非空或譯檔缺;`_meta.json` 復用 `_jar_lang_needs_translation`。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_oracle_wiki_scan.py
import json, zipfile
from modpack_translator.pipeline.scanner import ModpackScanner

def _make(tmp_path):
    jar = tmp_path / "oritech.jar"
    base = "assets/oracle_index/books/oritech"
    mdx = "---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\nThe chainsaw is a fast tool for cutting wood.\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(f"{base}/content/equipment/chainsaw.mdx", mdx)
        zf.writestr(f"{base}/content/_meta.json", json.dumps({"equipment": "Equipment"}))
        zf.writestr(f"{base}/sinytra-wiki.json", json.dumps({"id": "oritech"}))  # 非目標
    return jar

def test_scan_emits_oracle_mdx_and_meta_targets(tmp_path):
    jar = _make(tmp_path)
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    by_fmt = {}
    for t in targets:
        by_fmt.setdefault(t.format, []).append(t)
    mdx = by_fmt.get("oracle_mdx", [])
    meta = [t for t in by_fmt.get("json_lang", []) if t.path_in_jar.endswith("_meta.json")]
    assert len(mdx) == 1
    assert mdx[0].target_path_in_jar == "assets/oracle_index/books/oritech/translated/zh_tw/content/equipment/chainsaw.mdx"
    assert mdx[0].existing_path_in_jar is None
    assert len(meta) == 1
    assert meta[0].target_path_in_jar == "assets/oracle_index/books/oritech/translated/zh_tw/content/_meta.json"

def test_scan_skips_sinytra_manifest_and_translated_tree(tmp_path):
    jar = _make(tmp_path)
    with zipfile.ZipFile(jar, "a") as zf:  # 加一個已存在的 translated 檔,不應被當來源
        zf.writestr("assets/oracle_index/books/oritech/translated/zh_tw/content/x.mdx", "---\r\ntitle: Y\r\n---\r\n")
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    srcs = [t.path_in_jar for t in targets]
    assert not any("sinytra-wiki.json" in s for s in srcs)
    assert not any("/translated/" in s for s in srcs)
```

- [ ] **Step 2: 執行確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_oracle_wiki_scan.py -v`
Expected: FAIL（未產生 oracle_mdx target）

- [ ] **Step 3: 實作最小程式碼**

`scanner.py` 頂部新增匯入:`from modpack_translator.pipeline import mdx`。
在 `_scan_jar` 的 `for name in names:` 迴圈,於既有 `if lang_ext:` / patchouli `elif` 之後,新增分支呼叫新方法;並新增方法:

```python
    def _scan_oracle_book(self, zf, name, parts, name_set, lang_code, glossary):
        # 路徑：assets/oracle_index/books/<book>/<root>/...；root∈{content,docs}；不在 translated 下
        if not (len(parts) >= 6 and parts[0] == "assets" and parts[1] == "oracle_index"
                and parts[2] == "books" and parts[4] in ("content", "docs")):
            return None
        if "translated" in parts:
            return None
        book = parts[3]
        root_idx = 4
        write = "/".join(parts[:root_idx] + ["translated", lang_code.lower()] + parts[root_idx:])
        existing = write if write in name_set else None

        if name.endswith(".mdx"):
            if not self._oracle_mdx_needs_translation(zf, name, existing, glossary):
                return None
            return TranslationTarget(
                source_file=None, path_in_jar=name, mod_id=book,
                format="oracle_mdx", output_mode="jar_inject",
                output_lang_code=lang_code,
                target_path_in_jar=write, existing_path_in_jar=existing,
            )
        if parts[-1] == "_meta.json":
            if not self._jar_lang_needs_translation(zf, name, existing, "json", glossary):
                return None
            return TranslationTarget(
                source_file=None, path_in_jar=name, mod_id=book,
                format="json_lang", output_mode="jar_inject",
                output_lang_code=lang_code,
                target_path_in_jar=write, existing_path_in_jar=existing,
            )
        return None

    def _oracle_mdx_needs_translation(self, zf, source_path, existing_path, glossary):
        if getattr(self, "_include_translated", False):
            return True
        try:
            source = mdx.extract_mdx(zf.read(source_path).decode("utf-8-sig"))
        except (KeyError, UnicodeDecodeError):
            return False
        if not source:
            return False
        existing = {}
        if existing_path and existing_path in zf.namelist():
            try:
                existing = mdx.extract_mdx(zf.read(existing_path).decode("utf-8-sig"))
            except (KeyError, UnicodeDecodeError):
                existing = {}
        return bool(diff_keys(source, existing, glossary=glossary))
```

在迴圈內接上(於 patchouli `elif` 之後):

```python
                    else:
                        target = self._scan_oracle_book(zf, name, parts, name_set, lang_code, glossary)
                        if target is not None:
                            target = TranslationTarget(
                                source_file=jar_path, path_in_jar=target.path_in_jar,
                                mod_id=target.mod_id, format=target.format,
                                output_mode=target.output_mode, output_lang_code=target.output_lang_code,
                                target_path_in_jar=target.target_path_in_jar,
                                existing_path_in_jar=target.existing_path_in_jar,
                            )
                            targets.append(target)
```

> 註:`_scan_oracle_book` 內 `source_file=None` 只為建構;實際 append 時用上面帶 `jar_path` 的複本。（實作時可直接把 `jar_path` 傳入 `_scan_oracle_book` 省去複本——擇一即可,測試不變。）

- [ ] **Step 4: 執行確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_oracle_wiki_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/scanner.py tests/test_oracle_wiki_scan.py
git commit -m "feat(scanner): 偵測 Oracle 書 mdx/_meta.json 並輸出 translated/zh_tw"
```

---

### Task 7: runner.py — 處理 oracle_mdx(端到端)

**Files:**
- Modify: `src/modpack_translator/pipeline/runner.py`
- Test: `tests/test_oracle_wiki_run.py`（Create）

**Interfaces:**
- Consumes: `mdx.extract_mdx`/`rebuild_mdx`、`patcher.write_jar_text`、`preprocessor.read_jar_text`、`jar_member_exists`、既有 `translate_dict`。`_meta.json` 走既有 `json_lang` 路徑,無需改。
- Produces: `process_target` 新增 `oracle_mdx` 分支(委派 `_process_oracle_mdx`);`read_target_strings`/`read_existing_target` 對 `oracle_mdx` 回傳 `extract_mdx` 結果。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_oracle_wiki_run.py
import zipfile
from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target

class _Fixed:
    glossary = None
    def __init__(self, reply): self.reply = reply
    def translate(self, text, cancel_check=None): return self.reply

def test_process_oracle_mdx_writes_translated_tree(tmp_path):
    jar = tmp_path / "oritech.jar"
    base = "assets/oracle_index/books/oritech"
    mdx = "---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\nThe chainsaw cuts wood fast.\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(f"{base}/content/equipment/chainsaw.mdx", mdx)
    [t] = [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "oracle_mdx"]
    process_target(t, _Fixed("鏈鋸快速砍樹。"), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        tgt = "assets/oracle_index/books/oritech/translated/zh_tw/content/equipment/chainsaw.mdx"
        assert tgt in zf.namelist()
        out = zf.read(tgt).decode("utf-8-sig")
    assert "鏈鋸快速砍樹。" in out         # 散文已翻
    assert "id: oritech:chainsaw" in out    # frontmatter 結構保留
    assert "type: item" in out
```

- [ ] **Step 2: 執行確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_oracle_wiki_run.py -v`
Expected: FAIL（`process_target` 未處理 `oracle_mdx`,不寫出目標）

- [ ] **Step 3: 實作最小程式碼**

`runner.py` 匯入區新增:`from modpack_translator.pipeline import mdx` 與 `from modpack_translator.pipeline.patcher import write_jar_text`;`from modpack_translator.pipeline.preprocessor import ..., read_jar_text`。

`process_target` 開頭(patchouli 判斷旁)新增:

```python
    if target.format == "oracle_mdx":
        return _process_oracle_mdx(target, translator, cache, retry_count, cancel_check, on_pair_done)
```

`read_target_strings` 新增分支:

```python
    elif target.format == "oracle_mdx":
        return mdx.extract_mdx(read_jar_text(target.source_file, target.path_in_jar))
```

`read_existing_target` 於 `jar_inject` 區塊新增:

```python
        if target.format == "oracle_mdx":
            if not existing_path:
                return {}
            try:
                return mdx.extract_mdx(read_jar_text(target.source_file, existing_path))
            except (KeyError, OSError):
                return {}
```

新增函式:

```python
def _process_oracle_mdx(target, translator, cache, retry_count=0, cancel_check=None, on_pair_done=None):
    raw = read_jar_text(target.source_file, target.path_in_jar)
    en = mdx.extract_mdx(raw)
    if not en:
        return 0, 0, 0, {}
    zh_existing = read_existing_target(target, target.output_lang_code)
    result, n_t, n_c, n_f, failed = translate_dict(
        en, zh_existing, translator, cache, retry_count, cancel_check, on_pair_done
    )
    merged = {**zh_existing, **result}
    should_write = bool(result) or (
        bool(zh_existing) and not jar_member_exists(target.source_file, target.target_path_in_jar)
    )
    if should_write:
        new_raw = rebuild_mdx(raw, merged)
        # 內容未變則不重寫(避免 re-run 無謂改動 jar)
        if not (jar_member_exists(target.source_file, target.target_path_in_jar)
                and read_jar_text(target.source_file, target.target_path_in_jar) == new_raw):
            write_jar_text(target.source_file, target.target_path_in_jar, new_raw)
    return n_t, n_c, n_f, failed
```

> `mdx` 需在 `_process_oracle_mdx` 用到 `rebuild_mdx`;上面已 `from ... import mdx`,故以 `mdx.rebuild_mdx` 呼叫或一併 `from ...mdx import extract_mdx, rebuild_mdx`。擇一,保持一致。

- [ ] **Step 4: 執行確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_oracle_wiki_run.py -v`
Expected: PASS

- [ ] **Step 5: 全套件回歸**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠（既有 + 新增測試）

- [ ] **Step 6: Commit**

```bash
git add src/modpack_translator/pipeline/runner.py tests/test_oracle_wiki_run.py
git commit -m "feat(runner): 處理 oracle_mdx(抽取→翻譯→重建→寫 translated/zh_tw)"
```

---

### Task 8: 真實 jar 驗證 + 版本號 + 同步 Downloads

**Files:**
- Modify: `src/modpack_translator/version.py`、`pyproject.toml`、`uv.lock`
- 同步:`C:/Users/user/Downloads/Modpack_Translator/src/modpack_translator/pipeline/{mdx.py,scanner.py,runner.py,patcher.py}`

**Interfaces:**
- Consumes: 全部前置 Task。

- [ ] **Step 1: 對真實 oritech jar 冒煙測試(唯讀,先不寫)**

用 venv 跑一次性腳本:掃描 `oritech-neoforge-1.21.1-1.2.8.jar`,確認產生約 152 個 `oracle_mdx` + 若干 `_meta.json` target,且 `target_path_in_jar` 皆在 `translated/zh_tw/content/…`;抽一篇 `extract_mdx` 檢查有散文、無 JSX/ref 外洩。
Expected: target 數量合理、路徑正確、抽取內容乾淨。（此步僅驗證,不修改遊戲 jar;實際翻譯由使用者跑工具、關閉遊戲後進行。）

- [ ] **Step 2: 版本號 bump(patch)**

`version.py`、`pyproject.toml`、`uv.lock` 的 `1.7.2` → `1.7.3`。

- [ ] **Step 3: 全套件確認**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全綠。

- [ ] **Step 4: 同步 Downloads 執行版(保留 CRLF)**

把 repo 的 `mdx.py`(新檔)、`scanner.py`、`runner.py`、`patcher.py` 以 CRLF 寫入 Downloads 對應路徑(比照既有同步作法)。

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/version.py pyproject.toml uv.lock
git commit -m "chore: Oracle wiki MDX 支援完成,版本號 1.7.3 + 同步 Downloads"
```

---

## Self-Review

**1. Spec coverage**
- 偵測 content/docs 的 mdx+_meta → Task 6。docs/ 型書:偵測已含 `parts[4] in {content,docs}`,寫入 `translated/zh_tw/<root>/`;spec 待確認事項(docs 譯文路徑)在 Task 8 Step 1 真實驗證時一併確認;若 viewer 不吃 docs 路徑,縮回只 content(移除 `"docs"`),不影響 oritech。
- MDX 切段(frontmatter/正文/JSX/連結/保留)→ Task 1–4。
- translate_dict 重用 + glossary/快取/回退 → Task 7(沿用既有函式)。
- 交付 translated/zh_tw + jar_inject → Task 5(write_jar_text)+ Task 6(路徑)+ Task 7(寫出)。
- _meta.json 復用 json_lang → Task 6(format="json_lang")。
- 測試/冪等/回退 → 各 Task 測試 + Task 7 的「內容未變不重寫」。
- 版本號 + Downloads 同步 → Task 8。

**2. Placeholder scan**：無 TBD/TODO;每個程式步驟均含實際程式碼與可執行指令。

**3. Type consistency**:`extract_mdx`/`rebuild_mdx`/`_segments`/`_push_text`/`_key`/`_body_segments`/`_push_paragraph`/`_jsx_block_end`/`_scan_oracle_book`/`_oracle_mdx_needs_translation`/`_process_oracle_mdx`/`write_jar_text` 全程一致;`TranslationTarget` 沿用既有欄位(含前次新增的 `existing_path_in_jar`)。

## 已知限制(v1)

- MDX 段落鍵為結構位置序(`s0,s1,…`);若模組更新原文結構後再跑,位置序可能錯位。安全網:內容快取(sha256(原文))提供內容正確的重用;`is_usable_translation` 的 token 保留檢查攔下多數錯位;最壞情況為「重翻」或「保留舊譯」,不會弄壞渲染。
- JSX 僅涵蓋實測 4 種(Callout/center/ModAsset/CraftingRecipe);未知成對元件會被當自閉合找 `/>`,靠 round-trip 測試與 per-段回退兜底。
