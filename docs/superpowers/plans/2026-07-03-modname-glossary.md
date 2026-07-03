# 模組名譯名與自訂用語庫 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓模組名稱（Twilight Forest 等）翻成通行繁中譯名：三層用語庫（自訂 > 模組名 > 官方）合併走既有 Glossary 管線，加上事後強制替換（enforce）、三入口守門與既有快取/譯文的零成本自我遷移。

**Architecture:** 不新造替換系統；擴充 `pipeline/glossary.py` 的 `Glossary` 加 `enforce()` 與合併載入器，`preprocessor.is_usable_translation`/`diff_keys` 加 `glossary` 守門參數，`runner`/`batch_prefill` 在快取讀取、模型輸出、既有譯文三個入口接線。GUI 選項區三列併兩列，加「模組名譯名」checkbox 與「自訂用語」表格對話框。

**Tech Stack:** Python 3.11+、pytest、PySide6（GUI）。無新依賴。

**Spec:** `docs/superpowers/specs/2026-07-03-modname-glossary-design.md`

## Global Constraints

- 遷移全程零 API 成本：快取正規化、守門重翻（被 exact_match 短路）、既有譯文 enforce 都不呼叫模型。
- enforce 區分大小寫；單字詞條目只在整串（trim 後）等於該詞時替換；多字詞條目句中即替換，且詞前若為「CJK 字元＋單一半形空格」則一併吃掉該空格（中文與譯名間不留突兀空白）；該詞譯名已在譯文中則跳過（保護「中文名(English)」夾註）；替換後 `_preserves_required_tokens` 失敗退回原譯文。
- 合併優先序：自訂 > 模組名 > 官方；自訂條目譯名空字串 = 刪除該詞條。
- GUI 主視窗淨高度不得增加：選項群組三列（checkbox／重試／用語庫）併成兩列。
- 動態（機器學習）詞彙層不在本計畫範圍（見上下文翻譯計畫）。
- 專案註解/文案用繁體中文，風格比照既有檔案。
- 測試指令：`uv run pytest tests/<file> -v`（在 `C:\myspace\Modpack_Translator` 執行）。

---

### Task 1: `Glossary.enforce()` 事後替換

**Files:**
- Modify: `src/modpack_translator/pipeline/glossary.py`
- Test: `tests/test_glossary_enforce.py`（新檔）

**Interfaces:**
- Produces: `Glossary.enforce(text: str) -> str`（純文字轉換，無 token 檢查——那是 Task 5 呼叫端的責任）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_glossary_enforce.py
from __future__ import annotations

from modpack_translator.pipeline.glossary import Glossary


def _g() -> Glossary:
    return Glossary({
        "Twilight Forest": "暮光森林",
        "Applied Energistics 2": "應用能源2",
        "Create": "機械動力",
    })


def test_multiword_replaced_mid_sentence():
    assert _g().enforce("歡迎來到 Twilight Forest！") == "歡迎來到暮光森林！"


def test_multiword_plural_tolerated():
    assert _g().enforce("探索 Twilight Forests 區域") == "探索暮光森林 區域"


def test_singleword_only_replaced_when_whole_string():
    g = _g()
    assert g.enforce("Create") == "機械動力"
    assert g.enforce("  Create\n") == "  機械動力\n"
    # 句中單字詞不動（避免動詞 create/標題 Create New World 誤傷）
    assert g.enforce("Create New World") == "Create New World"


def test_case_sensitive():
    g = _g()
    assert g.enforce("please create a farm") == "please create a farm"
    assert g.enforce("歡迎來到 twilight forest") == "歡迎來到 twilight forest"


def test_annotation_style_skipped():
    # 譯名已出現在譯文中：視為刻意的中英夾註，不重複替換
    assert _g().enforce("暮光森林(Twilight Forest)入門") == "暮光森林(Twilight Forest)入門"


def test_word_boundary_not_partial():
    g = Glossary({"Aether": "天境"})
    assert g.enforce("Aethersteel 合金") == "Aethersteel 合金"


def test_empty_glossary_noop():
    assert Glossary({}).enforce("Twilight Forest") == "Twilight Forest"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_glossary_enforce.py -v`
Expected: FAIL，`AttributeError: 'Glossary' object has no attribute 'enforce'`

- [ ] **Step 3: 實作 enforce**

在 `glossary.py` 的 `Glossary.__init__` 中新增一行（`self._pattern` 之後）：

```python
        self._enforce_pattern: re.Pattern[str] | None = None
        self._enforce_ready = False
```

在 `format_block` 方法之前新增兩個方法：

```python
    def _enforce_compiled(self) -> re.Pattern[str] | None:
        """句中替換用的 pattern：只含多字詞條目、區分大小寫。無多字詞時 None。

        詞本體置於 group(1)、複數 (?:e?s)? 在群組外。詞前若為「CJK 字元＋
        單一半形空格」，把該空格一併納入 match：中文與譯名間不留突兀空白
        （英文語境如 "of Twilight Forest" 的空格因前字非 CJK 而保留）。
        """
        if not self._enforce_ready:
            multi = sorted(
                (t for t in self.terms if " " in t.strip()), key=len, reverse=True
            )
            if multi:
                alternation = "|".join(re.escape(term) for term in multi)
                self._enforce_pattern = re.compile(
                    r"(?:(?<=[㐀-鿿]) )?"
                    r"(?<![A-Za-z0-9])(" + alternation + r")(?:e?s)?(?![A-Za-z0-9])"
                )
            self._enforce_ready = True
        return self._enforce_pattern

    def enforce(self, text: str) -> str:
        """把譯文中殘留的英文詞彙替換為譯名（事後保證）。

        區分大小寫（避免動詞 create 誤傷）、整詞邊界、長詞優先。多字詞條目
        句中即替換（並吃掉中文與詞之間的單一空白）；單字詞條目只在整串
        （trim 後）完全等於該詞時替換，句中交給 prompt 注入讓模型判斷語境。
        該詞譯名已出現在譯文中則跳過（保護「中文名(English)」夾註）。純文字
        轉換，token 保全由呼叫端（runner._enforce_glossary）負責。

        group(1) 恰為某個 alternation 詞（大小寫敏感、複數在群組外），故可用
        self.terms.get(group(1)) 直接取譯名，不需再正規化。
        """
        if not self.terms:
            return text
        m = _EXACT_WS_RE.match(text)
        zh_exact = self.terms.get(m.group(2))
        if zh_exact is not None:
            return f"{m.group(1)}{zh_exact}{m.group(3)}"
        pattern = self._enforce_compiled()
        if pattern is None:
            return text

        def _sub(match: re.Match[str]) -> str:
            zh = self.terms.get(match.group(1))
            if zh is None or zh in text:
                return match.group(0)
            return zh

        return pattern.sub(_sub, text)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_glossary_enforce.py tests/test_glossary.py -v`
Expected: 全部 PASS（既有 test_glossary.py 不得回歸）

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/glossary.py tests/test_glossary_enforce.py
git commit -m "feat: Glossary.enforce 事後替換（區分大小寫、單字詞整串限定、夾註保護）"
```

---

### Task 2: 自訂用語 IO 與三層合併載入器

**Files:**
- Modify: `src/modpack_translator/pipeline/glossary.py`
- Test: `tests/test_glossary_merge.py`（新檔）

**Interfaces:**
- Produces:
  - `load_custom_terms(path: str | Path | None) -> dict[str, str]`（缺檔/壞檔回空 dict；保留空字串譯名的條目）
  - `save_custom_terms(path: str | Path, terms: dict[str, str]) -> None`
  - `load_merged_glossary(official_path, modnames_path, custom_path) -> Glossary | None`（三參數皆 `str | Path | None`）
  - `modnames_glossary_path(lang_code: str) -> Path`（`assets/glossary/modnames_{lang_code}.json`）
  - `default_custom_glossary_path() -> Path`（`Path.home() / ".modpack_translator" / "custom_glossary.json"`，不依賴 Qt，GUI/CLI 共用，更新程式不會蓋掉）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_glossary_merge.py
from __future__ import annotations

import json

from modpack_translator.pipeline.glossary import (
    load_custom_terms,
    load_merged_glossary,
    save_custom_terms,
)


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_merge_priority_custom_over_modnames_over_official(tmp_path):
    official = _write(tmp_path, "official.json", {"Nether": "地獄", "Creeper": "苦力怕"})
    modnames = _write(tmp_path, "modnames.json", {"Create": "機械動力", "Nether": "官方被蓋"})
    custom = _write(tmp_path, "custom.json", {"Create": "創造模式錯譯修正"})
    g = load_merged_glossary(official, modnames, custom)
    assert g.terms["Creeper"] == "苦力怕"
    assert g.terms["Nether"] == "官方被蓋"        # 模組名層 > 官方層
    assert g.terms["Create"] == "創造模式錯譯修正"  # 自訂層 > 模組名層


def test_custom_empty_translation_deletes_term(tmp_path):
    modnames = _write(tmp_path, "modnames.json", {"Create": "機械動力", "Quark": "夸克"})
    custom = _write(tmp_path, "custom.json", {"create": ""})  # 大小寫不同也要刪
    g = load_merged_glossary(None, modnames, custom)
    assert "Create" not in g.terms
    assert g.terms["Quark"] == "夸克"


def test_missing_files_tolerated(tmp_path):
    modnames = _write(tmp_path, "modnames.json", {"Create": "機械動力"})
    g = load_merged_glossary(tmp_path / "no.json", modnames, tmp_path / "no2.json")
    assert g.terms == {"Create": "機械動力"}
    assert load_merged_glossary(None, None, None) is None


def test_custom_terms_roundtrip(tmp_path):
    p = tmp_path / "sub" / "custom.json"
    save_custom_terms(p, {"Create": "機械動力", "Quark": ""})
    assert load_custom_terms(p) == {"Create": "機械動力", "Quark": ""}
    assert load_custom_terms(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    assert load_custom_terms(tmp_path / "bad.json") == {}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_glossary_merge.py -v`
Expected: FAIL，`ImportError: cannot import name 'load_custom_terms'`

- [ ] **Step 3: 實作**

在 `glossary.py` 的 `load_glossary` 之後新增：

```python
def load_custom_terms(path: str | Path | None) -> dict[str, str]:
    """讀取自訂用語 JSON（en→zh）。zh 空字串是合法值（＝刪除該詞條），
    必須保留給合併器判讀。缺檔、壞檔回空 dict。"""
    if not path:
        return {}
    p = Path(path).expanduser()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        en.strip(): zh.strip()
        for en, zh in data.items()
        if isinstance(en, str) and isinstance(zh, str) and en.strip()
    }


def save_custom_terms(path: str | Path, terms: dict[str, str]) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(terms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_merged_glossary(
    official_path: str | Path | None,
    modnames_path: str | Path | None,
    custom_path: str | Path | None,
) -> Glossary | None:
    """三層合併：官方 → 模組名 → 自訂，後者覆蓋前者；自訂空譯名刪除詞條。
    大小寫不敏感比對既有鍵：自訂覆蓋既有詞時保留既有鍵的原始大小寫
    （模組名/官方多為專有名詞正式大小寫、enforce 為大小寫敏感，須維持），
    只更新譯名；既有無此詞才以自訂鍵新增。全空時回 None。"""
    terms: dict[str, str] = {}
    for p in (official_path, modnames_path):
        layer = load_glossary(p)
        if layer is not None:
            terms.update(layer.terms)
    for en, zh in load_custom_terms(custom_path).items():
        existing = [k for k in terms if k.lower() == en.lower()]
        if zh:
            if existing:
                for k in existing:
                    terms[k] = zh
            else:
                terms[en] = zh
        else:
            for k in existing:
                del terms[k]
    return Glossary(terms) if terms else None


def modnames_glossary_path(lang_code: str) -> Path:
    return _GLOSSARY_DIR / f"modnames_{lang_code}.json"


def default_custom_glossary_path() -> Path:
    """使用者級自訂用語檔：住家目錄下，更新/重灌程式都不會被清掉。"""
    return Path.home() / ".modpack_translator" / "custom_glossary.json"
```

注意：`available_glossaries` 用 `{lang_code}_*.json` glob，`modnames_zh_tw.json` 不符合 `zh_tw_` 前綴，不會混進官方版本下拉選單——不需改動。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_glossary_merge.py tests/test_glossary.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/glossary.py tests/test_glossary_merge.py
git commit -m "feat: 自訂用語 IO 與三層用語庫合併載入器"
```

---

### Task 3: 預建模組名對照表

**Files:**
- Create: `assets/glossary/modnames_zh_tw.json`
- Test: `tests/test_glossary_merge.py`（追加一個 smoke test）

**Interfaces:**
- Produces: 預建資產檔，`load_merged_glossary(None, modnames_glossary_path("zh_tw"), None)` 可載入

- [ ] **Step 1: 寫失敗測試（追加到 tests/test_glossary_merge.py）**

```python
def test_prebuilt_modnames_asset_loads():
    from modpack_translator.pipeline.glossary import modnames_glossary_path

    p = modnames_glossary_path("zh_tw")
    assert p.exists()
    g = load_merged_glossary(None, p, None)
    assert g is not None
    assert g.terms["Twilight Forest"] == "暮光森林"
    assert g.terms["Create"] == "機械動力"
    assert len(g.terms) >= 40
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_glossary_merge.py::test_prebuilt_modnames_asset_loads -v`
Expected: FAIL，`assert p.exists()` 為 False

- [ ] **Step 3: 建立資產檔**

建立 `assets/glossary/modnames_zh_tw.json`（UTF-8、無 BOM）。收錄原則：寧缺勿錯，只收社群通行、爭議低的譯名；用語取 zh_tw 慣用字（儲存、網路）；冷門模組留給自訂表。初版內容：

```json
{
  "Twilight Forest": "暮光森林",
  "The Twilight Forest": "暮光森林",
  "Applied Energistics 2": "應用能源2",
  "Applied Energistics": "應用能源",
  "Create": "機械動力",
  "Botania": "植物魔法",
  "Mekanism": "通用機械",
  "Immersive Engineering": "沉浸工程",
  "Immersive Petroleum": "沉浸石油",
  "Blood Magic": "血魔法",
  "Ars Nouveau": "新生魔藝",
  "Tinkers' Construct": "匠魂",
  "Tinkers Construct": "匠魂",
  "Thermal Expansion": "熱力膨脹",
  "Thermal Foundation": "熱力基礎",
  "Thaumcraft": "神秘時代",
  "Industrial Foregoing": "工業先鋒",
  "Forestry": "林業",
  "Draconic Evolution": "龍之進化",
  "Farmer's Delight": "農夫樂事",
  "Sophisticated Backpacks": "精妙背包",
  "Sophisticated Storage": "精妙儲存",
  "Storage Drawers": "儲物抽屜",
  "Iron Chests": "鐵箱子",
  "Quark": "夸克",
  "Astral Sorcery": "星輝魔法",
  "PneumaticCraft": "氣動工藝",
  "Alex's Mobs": "亞歷克斯的生物",
  "Supplementaries": "錦上添花",
  "Mystical Agriculture": "神秘農業",
  "Aquaculture": "水產養殖",
  "The Aether": "天境",
  "Aether": "天境",
  "Apotheosis": "神化",
  "Nature's Aura": "自然靈氣",
  "Compact Machines": "緊湊機械",
  "EvilCraft": "邪惡工藝",
  "Extra Utilities": "更多實用設備",
  "GregTech": "格雷科技",
  "Environmental Tech": "環境科技",
  "Torchmaster": "火把大師",
  "Building Gadgets": "建築小工具",
  "Mining Gadgets": "採礦小工具",
  "JourneyMap": "旅行地圖",
  "Serene Seasons": "寧靜四季",
  "Hostile Neural Networks": "敵對神經網路"
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_glossary_merge.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add assets/glossary/modnames_zh_tw.json tests/test_glossary_merge.py
git commit -m "feat: 預建常見模組名 zh_tw 對照表（初版 46 條，寧缺勿錯）"
```

---

### Task 4: `is_usable_translation` / `diff_keys` 守門參數

**Files:**
- Modify: `src/modpack_translator/pipeline/preprocessor.py:109-139`（`is_usable_translation`）、`:645-658`（`diff_keys`）
- Test: `tests/test_preprocessor_glossary_gate.py`（新檔）

**Interfaces:**
- Consumes: `Glossary.exact_match(text) -> str | None`（既有）
- Produces:
  - `is_usable_translation(source, target, key=None, *, accept_identical_proper_noun=False, glossary=None) -> bool`
  - `diff_keys(en_dict, zh_dict, glossary=None) -> set[str]`
  - 守門語義：`glossary` 非 None 且 `glossary.exact_match(source)` 命中時，`dst == src` 的原樣返回一律回 False（凌駕專有名詞豁免與任務標題豁免）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_preprocessor_glossary_gate.py
from __future__ import annotations

from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.preprocessor import diff_keys, is_usable_translation

G = Glossary({"Building Gadgets": "建築小工具", "Twilight Forest": "暮光森林"})


def test_identical_hit_rejected_even_with_proper_noun_exemption():
    assert is_usable_translation(
        "Building Gadgets", "Building Gadgets",
        accept_identical_proper_noun=True, glossary=G,
    ) is False


def test_identical_hit_rejected_for_quest_title_key():
    assert is_usable_translation(
        "Building Gadgets", "Building Gadgets",
        key="quest.1A2B3C.title", glossary=G,
    ) is False


def test_identical_miss_keeps_existing_behavior():
    # 未命中用語庫的專有名詞式標題：維持既有放行行為
    assert is_usable_translation(
        "Mining Gadgets", "Mining Gadgets",
        accept_identical_proper_noun=True, glossary=G,
    ) is True


def test_no_glossary_keeps_existing_behavior():
    assert is_usable_translation(
        "Building Gadgets", "Building Gadgets",
        accept_identical_proper_noun=True,
    ) is True


def test_translated_value_not_affected_by_gate():
    assert is_usable_translation("Building Gadgets", "建築小工具", glossary=G) is True


def test_diff_keys_includes_identical_hit_title():
    en = {"quest.1A2B3C.title": "Building Gadgets"}
    zh = {"quest.1A2B3C.title": "Building Gadgets"}
    assert diff_keys(en, zh, glossary=G) == {"quest.1A2B3C.title"}
    assert diff_keys(en, zh) == set()  # 無 glossary 時維持既有行為
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_preprocessor_glossary_gate.py -v`
Expected: FAIL，`TypeError: is_usable_translation() got an unexpected keyword argument 'glossary'`

- [ ] **Step 3: 實作**

`preprocessor.py` 檔頭 import 區加（僅型別用途，避免執行期循環匯入）：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modpack_translator.pipeline.glossary import Glossary
```

`is_usable_translation` 簽名與 `dst == src` 分支改為：

```python
def is_usable_translation(
    source: str,
    target: str,
    key: str | None = None,
    *,
    accept_identical_proper_noun: bool = False,
    glossary: "Glossary | None" = None,
) -> bool:
```

```python
    if dst == src:
        if not needs_visible_translation:
            return True
        # 用語庫守門：整串命中用語庫的原樣返回一律不放行——凌駕下方的
        # 專有名詞豁免與任務標題豁免。呼叫端以 exact_match 譯名取代
        # （runner._translate_validated），或讓該鍵進 diff 重翻（零 API 成本）。
        if glossary is not None and glossary.exact_match(source) is not None:
            return False
        # 任務標題常刻意保留英文專有名詞（模組名、玩家 ID）。既有翻譯檔中
        # （以下註解與邏輯不變）
```

`diff_keys` 改為：

```python
def diff_keys(
    en_dict: dict[str, str],
    zh_dict: dict[str, str],
    glossary: "Glossary | None" = None,
) -> set[str]:
    """Return keys that are missing from zh or still identical to en."""
    translatable_keys = {
        k for k, value in en_dict.items()
        if _is_translatable_entry(k, value)
    }
    missing = translatable_keys - set(zh_dict)
    untranslated = {
        k
        for k in translatable_keys
        if k in zh_dict
        and not is_usable_translation(en_dict[k], zh_dict[k], key=k, glossary=glossary)
    }
    return missing | untranslated
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_preprocessor_glossary_gate.py tests/test_preprocessor_lang_compat.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/preprocessor.py tests/test_preprocessor_glossary_gate.py
git commit -m "feat: is_usable_translation/diff_keys 用語庫守門參數"
```

---

### Task 5: runner 守門與 enforce 接線（模型輸出＋快取讀取）

**Files:**
- Modify: `src/modpack_translator/pipeline/runner.py`（import 區、`_translate_validated` :97-119、`translate_dict` :242-294）
- Test: `tests/test_runner_glossary.py`（追加）

**Interfaces:**
- Consumes: Task 1 `Glossary.enforce`、Task 4 `glossary` 參數
- Produces: `_enforce_glossary(glossary, source: str, translated: str) -> str`（token 保全失敗退回原譯文；供 batch_prefill 匯入）

- [ ] **Step 1: 寫失敗測試（追加到 tests/test_runner_glossary.py）**

```python
def test_cached_identical_modname_replaced_without_llm():
    g = Glossary({"Twilight Forest": "暮光森林"})
    tr = RecordingTranslator(g)
    ck = cache_key("Twilight Forest")
    cache = {ck: "Twilight Forest"}  # 舊「英文→英文」快取
    result, n_t, n_c, n_f, failed = translate_dict({"k": "Twilight Forest"}, {}, tr, cache)
    assert result == {"k": "暮光森林"}
    assert tr.calls == []           # 守門後由 exact_match 短路，不呼叫模型
    assert cache[ck] == "暮光森林"


def test_cached_translation_with_leftover_name_enforced():
    g = Glossary({"Twilight Forest": "暮光森林"})
    tr = RecordingTranslator(g)
    src = "Welcome to the Twilight Forest!"
    ck = cache_key(src)
    cache = {ck: "歡迎來到 Twilight Forest！"}
    result, *_ = translate_dict({"k": src}, {}, tr, cache)
    assert result == {"k": "歡迎來到暮光森林！"}
    assert cache[ck] == "歡迎來到暮光森林！"
    assert tr.calls == []


def test_model_identical_return_enforced():
    # 原文帶驚嘆號 → exact_match 不短路 → 送模型 → 模型原樣返回
    # （帶標點的原樣返回不觸發守門，但走專有名詞豁免後由 enforce 換上譯名）
    g = Glossary({"Twilight Forest": "暮光森林"})

    class EchoTranslator:
        glossary = g

        def translate(self, text, cancel_check=None):
            return text

    result, n_t, *_ = translate_dict({"k": "Twilight Forest!"}, {}, EchoTranslator(), {})
    assert result == {"k": "暮光森林!"}


def test_enforce_glossary_applies_and_preserves_tokens():
    from modpack_translator.pipeline.runner import _enforce_glossary
    g = Glossary({"Twilight Forest": "暮光森林"})
    # CJK 後緊接的半形空白一併吃掉（enforce 的 CJK-空白處理）
    assert _enforce_glossary(g, "Twilight Forest ahead", "前方 Twilight Forest") == "前方暮光森林"
    # 含 %s 硬性 token 的譯文：替換模組名但保留 token（_preserves_required_tokens 通過）
    assert _enforce_glossary(
        g, "Twilight Forest %s", "前方 Twilight Forest %s"
    ) == "前方暮光森林 %s"
    # glossary=None 直接原樣返回
    assert _enforce_glossary(None, "x", "y") == "y"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_runner_glossary.py -v`
Expected: 新增測試 FAIL（`ImportError: _enforce_glossary` 或斷言失敗），既有 4 個測試 PASS

- [ ] **Step 3: 實作**

`runner.py` 的 preprocessor import 清單加入 `_preserves_required_tokens`。

`cache_key` 之後新增：

```python
def _enforce_glossary(glossary: Any, source: str, translated: str) -> str:
    """對已通過驗證的譯文套用用語庫事後保證。

    只在替換後仍保留全部硬性 token 時採用（fail-safe：替換到還原後
    token 內容的極端情況退回原譯文，交由既有驗證處理）。
    """
    if glossary is None:
        return translated
    enforced = glossary.enforce(translated)
    if enforced == translated or not _preserves_required_tokens(source, enforced):
        return translated
    return enforced
```

`_translate_validated` 全文改為：

```python
def _translate_validated(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    static = _static_translation(source)
    if static is not None and is_usable_translation(source, static):
        return static, True

    # 整串正好是用語庫詞彙：直接用譯名，不呼叫模型（語義同靜態表短路）
    glossary = getattr(translator, "glossary", None)
    if glossary is not None:
        official = glossary.exact_match(source)
        if official is not None and is_usable_translation(source, official):
            return official, True

    encoded, tokens = encode(source)
    final, ok = _translate_single(translator, encoded, tokens, retry_count, cancel_check)
    # 模型輸出關卡開啟專有名詞豁免：模型對模組名、人名等原樣返回是正確判斷；
    # 但整串命中用語庫的原樣返回不放行（守門），改以官方譯名取代。
    # 已放行的輸出再套 enforce，替換句中殘留的英文詞彙。
    if ok and is_usable_translation(
        source, final, accept_identical_proper_noun=True, glossary=glossary
    ):
        return _enforce_glossary(glossary, source, final), True
    if glossary is not None:
        official = glossary.exact_match(source)
        if official is not None and is_usable_translation(source, official):
            return official, True
    return source, False
```

`translate_dict` 的快取分支改為（函式開頭先取 `glossary = getattr(translator, "glossary", None)`，`to_translate` 一行改傳 `glossary`）：

```python
    to_translate = diff_keys(en_dict, zh_existing, glossary=glossary)
```

```python
        ck = cache_key(src)
        if ck in cache and is_usable_translation(
            src, cache[ck], accept_identical_proper_noun=True, glossary=glossary
        ):
            value = _enforce_glossary(glossary, src, cache[ck])
            if value != cache[ck]:
                cache[ck] = value
            result[key] = value
            n_cached += 1
            if on_pair_done is not None:
                on_pair_done(1)
            continue
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_runner_glossary.py tests/test_runner_fatal.py tests/test_failed_items_regression.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/runner.py tests/test_runner_glossary.py
git commit -m "feat: runner 守門與 enforce 接線（模型輸出、快取讀取、diff 傳遞）"
```

---

### Task 6: 既有譯文遷移（工具產物 enforce）＋ Patchouli 同步

**Files:**
- Modify: `src/modpack_translator/pipeline/runner.py`（`translate_dict`、`_process_patchouli` :400-455）
- Test: `tests/test_runner_glossary.py`（追加）

**Interfaces:**
- Consumes: Task 5 `_enforce_glossary`
- Produces: `translate_dict` 對「既有譯文 == 快取值（工具產物）」的條目套 enforce 並寫入 result / cache；與快取不一致（可能人工修改）不動

- [ ] **Step 1: 寫失敗測試（追加到 tests/test_runner_glossary.py）**

```python
def test_existing_tool_translation_enforced():
    g = Glossary({"Twilight Forest": "暮光森林"})
    tr = RecordingTranslator(g)
    src = "Welcome to the Twilight Forest!"
    old = "歡迎來到 Twilight Forest！"
    cache = {cache_key(src): old}
    # 既有譯文與快取一致 → 工具產物 → enforce 修復並寫回 result 與 cache
    result, *_ = translate_dict({"k": src}, {"k": old}, tr, cache)
    assert result["k"] == "歡迎來到暮光森林！"
    assert cache[cache_key(src)] == "歡迎來到暮光森林！"
    assert tr.calls == []


def test_existing_human_translation_untouched():
    g = Glossary({"Twilight Forest": "暮光森林"})
    tr = RecordingTranslator(g)
    src = "Welcome to the Twilight Forest!"
    human = "歡迎來到 Twilight Forest！"
    # 快取中沒有對應值（或值不同）→ 視為人工翻譯 → 不動
    result, *_ = translate_dict({"k": src}, {"k": human}, tr, {})
    assert "k" not in result
    assert tr.calls == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_runner_glossary.py -v -k existing`
Expected: `test_existing_tool_translation_enforced` FAIL（result 中沒有 "k"）

- [ ] **Step 3: 實作**

`translate_dict` 中 `to_translate = diff_keys(...)` 之後、主迴圈之前插入：

```python
    # 既有譯文遷移：與快取值一致代表是本工具翻的（非人工修改），
    # 套 enforce 修復句中殘留的英文詞彙並寫回；不一致者一律不動。
    # 不計入統計——是零成本的順帶修復，非本輪翻譯量。
    if glossary is not None:
        for key, existing_value in zh_existing.items():
            if key in to_translate:
                continue
            src = en_dict.get(key)
            if src is None:
                continue
            ck = cache_key(src)
            if cache.get(ck) != existing_value:
                continue
            enforced = _enforce_glossary(glossary, src, existing_value)
            if enforced != existing_value:
                result[key] = enforced
                cache[ck] = enforced
```

`_process_patchouli` 同步三處：

1. 函式開頭（`source_page` 讀取前）加 `glossary = getattr(translator, "glossary", None)`。
2. 既有譯文採納迴圈（原 :419-422）改為：

```python
    for path_key, existing_value in existing_strings.items():
        source_value = source_strings.get(path_key)
        if source_value is None:
            continue
        if not is_usable_translation(source_value, existing_value, glossary=glossary):
            continue
        ck = cache_key(source_value)
        if cache.get(ck) == existing_value:
            enforced = _enforce_glossary(glossary, source_value, existing_value)
            if enforced != existing_value:
                cache[ck] = enforced
                existing_value = enforced
        write_patchouli_text(page, path_key, existing_value)
```

3. 快取分支（原 :436-438）改為與 Task 5 `translate_dict` 相同形狀：

```python
        if ck in cache and is_usable_translation(
            src, cache[ck], accept_identical_proper_noun=True, glossary=glossary
        ):
            value = _enforce_glossary(glossary, src, cache[ck])
            if value != cache[ck]:
                cache[ck] = value
            write_patchouli_text(page, path_key, value)
```

（該分支其後的 `changed = True`、`n_cached += 1` 等行不變。）

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_runner_glossary.py tests/test_failed_items_regression.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/runner.py tests/test_runner_glossary.py
git commit -m "feat: 既有譯文零成本遷移（工具產物判定 enforce）＋ Patchouli 同步"
```

---

### Task 7: 快取正規化

**Files:**
- Modify: `src/modpack_translator/pipeline/runner.py`
- Test: `tests/test_runner_glossary.py`（追加）

**Interfaces:**
- Produces: `normalize_cache_with_glossary(cache: dict[str, str], glossary) -> int`（回傳覆寫條數；worker/CLI 在載入快取與用語庫後呼叫）

- [ ] **Step 1: 寫失敗測試**

```python
def test_normalize_cache_with_glossary():
    from modpack_translator.pipeline.runner import normalize_cache_with_glossary
    g = Glossary({"Twilight Forest": "暮光森林", "Create": "機械動力"})
    cache = {
        cache_key("Twilight Forest"): "Twilight Forest",  # 覆寫
        cache_key("Create"): "機械動力",                    # 已一致，不動
        cache_key("Other Text"): "其他譯文",                # 非詞彙槽位，不動
    }
    assert normalize_cache_with_glossary(cache, g) == 1
    assert cache[cache_key("Twilight Forest")] == "暮光森林"
    assert cache[cache_key("Other Text")] == "其他譯文"
    assert normalize_cache_with_glossary(cache, g) == 0  # 冪等
    assert normalize_cache_with_glossary(cache, None) == 0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_runner_glossary.py::test_normalize_cache_with_glossary -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 實作（runner.py，`_enforce_glossary` 之後）**

```python
def normalize_cache_with_glossary(cache: dict[str, str], glossary: Any) -> int:
    """快取正規化：快取 key 是 sha256(原文) 不存原文，但用語庫的詞我們知道
    原文——對每個詞算 cache_key 精準定位槽位，存在且不等於譯名就覆寫。
    每輪執行時呼叫、冪等、零 API 成本；只動既存槽位，不注入新條目。
    回傳覆寫條數。"""
    if glossary is None:
        return 0
    changed = 0
    for en, zh in glossary.terms.items():
        ck = cache_key(en)
        if ck in cache and cache[ck] != zh:
            cache[ck] = zh
            changed += 1
    return changed
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_runner_glossary.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/runner.py tests/test_runner_glossary.py
git commit -m "feat: 快取正規化（詞彙雜湊精準定位、冪等、零 API 成本）"
```

---

### Task 8: batch_prefill 接線

**Files:**
- Modify: `src/modpack_translator/pipeline/batch_prefill.py`（`collect_prefill_items` :140-155、`_process_batch` :397-408、runner import 區）
- Test: `tests/test_batch_prefill_glossary.py`（新檔）

**Interfaces:**
- Consumes: Task 5 `_enforce_glossary`（自 runner 匯入，batch_prefill 已匯入 runner 多個符號，無循環問題）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_batch_prefill_glossary.py
from __future__ import annotations

import threading

from modpack_translator.pipeline import batch_prefill as bp
from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.runner import cache_key

G = Glossary({"Twilight Forest": "暮光森林"})


def test_collect_rejects_poisoned_identical_cache(tmp_path):
    # 直接測 collect 的快取判斷邏輯所依賴的守門：
    # 舊「英文→英文」快取命中詞不再視為已翻譯 → 字串會被收集
    from modpack_translator.pipeline.preprocessor import is_usable_translation
    assert is_usable_translation(
        "Twilight Forest", "Twilight Forest",
        accept_identical_proper_noun=True, glossary=G,
    ) is False


def test_process_batch_enforces_leftover_names(monkeypatch):
    item = bp.PrefillItem(source="Welcome to the Twilight Forest!", ck="x")
    enc = bp._EncodedItem(item=item, encoded=item.source, tokens=[])
    monkeypatch.setattr(
        bp, "_request_batch_raw",
        lambda *a, **k: '[{"id": 0, "text": "歡迎來到 Twilight Forest!"}]',
    )
    res = bp._process_batch(
        None, None, "m", "sys", [enc], threading.Event(), lambda s: None, glossary=G,
    )
    assert res.results[0][1] == "歡迎來到暮光森林!"


def test_process_batch_identical_hit_not_accepted(monkeypatch):
    item = bp.PrefillItem(source="Twilight Forest", ck=cache_key("Twilight Forest"))
    enc = bp._EncodedItem(item=item, encoded=item.source, tokens=[])
    monkeypatch.setattr(
        bp, "_request_batch_raw",
        lambda *a, **k: '[{"id": 0, "text": "Twilight Forest"}]',
    )
    res = bp._process_batch(
        None, None, "m", "sys", [enc], threading.Event(), lambda s: None, glossary=G,
    )
    # 原樣返回命中守門 → 該條標記失敗 → 進逐條救援，由 runner 短路以譯名解決
    assert res.results[0][1] is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_batch_prefill_glossary.py -v`
Expected: `test_process_batch_enforces_leftover_names` FAIL（回傳仍含英文）、`test_process_batch_identical_hit_not_accepted` FAIL（回傳非 None）

- [ ] **Step 3: 實作**

batch_prefill.py 的 runner import 清單加入 `_enforce_glossary`。

`collect_prefill_items` 快取判斷（:145-147）改為：

```python
            if ck in cache and is_usable_translation(
                src, cache[ck], accept_identical_proper_noun=True, glossary=glossary
            ):
                continue
```

同函式 `diff_keys(en, zh)`（:140）改為 `diff_keys(en, zh, glossary=glossary)`。

`_process_batch` 逐條驗證（:403-407）改為：

```python
                candidate, ok = process(raw_text, enc.encoded, enc.tokens)
                if ok and is_usable_translation(
                    enc.item.source, candidate,
                    accept_identical_proper_noun=True, glossary=glossary,
                ):
                    final = _enforce_glossary(glossary, enc.item.source, candidate)
```

（逐條救援路徑走 `_translate_segmented_text` → `_translate_validated`，Task 5 已涵蓋，不需改。）

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_batch_prefill_glossary.py tests/test_batch_prefill.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/batch_prefill.py tests/test_batch_prefill_glossary.py
git commit -m "feat: 批次預翻譯接線用語庫守門與 enforce"
```

---

### Task 9: worker 與 CLI 接線（合併載入＋快取正規化＋掃描一致性）

**Files:**
- Modify: `src/modpack_translator/gui/worker.py`（`_filter_pending_targets` :45-56、`ScanWorker` :59-117、`TranslateWorker.run` :148-260）
- Modify: `scripts/translate_modpack.py`（頂層 import :15、CLI 自有的 `_filter_pending_targets` :106、`_dry_run_report` :120、scan/過濾呼叫 :180-181、glossary 載入區 :217-225、快取載入 :238-240）
- Modify: `src/modpack_translator/config.py`（`LanguageConfig` :82-87）

**Interfaces:**
- Consumes: Task 2 `load_merged_glossary`、Task 7 `normalize_cache_with_glossary`
- Produces: `ScanWorker.__init__(..., glossary: Glossary | None = None)`；`_filter_pending_targets(all_targets, lang_code, glossary=None)`（Task 10 的 GUI 會傳入）；CLI 的 `_filter_pending_targets`/`_dry_run_report` 同加 `glossary=None`

> **關鍵一致性約束：** GUI 與 CLI 的「掃描/過濾」都用 `diff_keys`。若過濾時不帶 glossary，
> 只含「命中詞英文標題」的檔案（如使用者實測包的 149 條）會被判為已翻而過濾掉、永不進入
> 翻譯/enforce，遷移就失效。因此**兩條路徑都必須在過濾前載入 glossary 並傳入**。CLI 現況是
> glossary 於 :217 才載入、卻在 :181 就過濾——本 Task 必須把 CLI 的 glossary 載入移到過濾之前。

- [ ] **Step 1: 實作 worker.py**

import 區把 `from modpack_translator.pipeline.glossary import load_glossary` 改為：

```python
from modpack_translator.pipeline.glossary import Glossary, load_merged_glossary
from modpack_translator.pipeline.runner import (
    ...,  # 既有匯入不變
    normalize_cache_with_glossary,
)
```

`_filter_pending_targets` 加參數並傳遞：

```python
def _filter_pending_targets(
    all_targets: list[TranslationTarget],
    lang_code: str,
    glossary: Glossary | None = None,
) -> list[TranslationTarget]:
    ...
        if diff_keys(strings, existing, glossary=glossary):
```

`ScanWorker.__init__` 加 `glossary: Glossary | None = None` 參數存為 `self._glossary`；`run()` 中 `_filter_pending_targets(...)` 與統計迴圈的 `diff_keys(strings, existing)` 都改傳 `glossary=self._glossary`。掃描若不帶 glossary，只含「命中詞英文標題」的檔案會被過濾掉、永遠不會被修復——這是掃描必須與翻譯同一套 glossary 的原因。

`TranslateWorker.run` 的 glossary 載入區（:170-177）改為：

```python
            # 用語庫：官方＋模組名＋自訂三層合併；載入與 regex 編譯皆在
            # worker 執行緒內，建構後不可變、跨執行緒安全
            glossary = None
            lang = self._cfg.language
            if lang.glossary_path or lang.modnames_glossary_path or lang.custom_glossary_path:
                glossary = load_merged_glossary(
                    lang.glossary_path, lang.modnames_glossary_path, lang.custom_glossary_path
                )
                if glossary is not None:
                    self.log.emit(f"已載入用語庫：{len(glossary.terms):,} 條（官方＋模組名＋自訂）")
                else:
                    self.log.emit("[警告] 無法載入用語庫，本次翻譯不使用用語庫。")

            # 快取正規化：依用語庫覆寫既有詞彙槽位（零 API 成本、冪等）
            fixed = normalize_cache_with_glossary(cache, glossary)
            if fixed:
                self.log.emit(f"已依用語庫正規化 {fixed:,} 條既有快取（零 API 成本）。")
                _flush_cache(cache_path, cache)
```

- [ ] **Step 2: 實作 CLI（scripts/translate_modpack.py）**

CLI 有自己一套掃描/過濾函式，且 glossary 現在載入得太晚（過濾在前、載入在後）。改法分四點。

**2a. 頂層 import**（:15）把 `from modpack_translator.pipeline.glossary import load_glossary` 改為（移除死 import、換成合併載入所需符號）：

```python
from modpack_translator.pipeline.glossary import (
    default_custom_glossary_path,
    load_merged_glossary,
    modnames_glossary_path,
)
from modpack_translator.pipeline.runner import normalize_cache_with_glossary  # 併入既有 runner import
```

**2b. CLI 的 `_filter_pending_targets`（:106）與 `_dry_run_report`（:120）各加 `glossary=None` 參數**，內部的 `diff_keys(strings, existing)` 改為 `diff_keys(strings, existing, glossary=glossary)`（與 worker.py 對稱）。

**2c. 把 glossary 載入整塊移到「scan/過濾（:180-181）之前」**，取代原 :217-225 的載入區（原處刪除）。載入邏輯：

```python
    glossary = None
    if not args.no_glossary:
        official_path = args.glossary or cfg.language.glossary_path
        modnames_path = cfg.language.modnames_glossary_path
        if modnames_path is None:
            mp = modnames_glossary_path(cfg.language.code)
            modnames_path = str(mp) if mp.exists() else None
        custom_path = cfg.language.custom_glossary_path or str(default_custom_glossary_path())
        glossary = load_merged_glossary(official_path, modnames_path, custom_path)
        if glossary is not None:
            print(f"已載入用語庫：{len(glossary.terms):,} 條（官方＋模組名＋自訂）")
        else:
            print("[警告] 無法載入任何用語庫，本次不使用用語庫。")
```

並把 scan/過濾呼叫（:181）改為傳入 glossary：
`_filter_pending_targets(all_targets, cfg.language.code, glossary)`；
若有 dry-run 分支呼叫 `_dry_run_report(...)`，同樣補上 `glossary`。

**2d. 快取正規化**：找到快取載入行（`_load_cache(`，約 :238-240）其後插入：

```python
    fixed = normalize_cache_with_glossary(cache, glossary)
    if fixed:
        print(f"已依用語庫正規化 {fixed:,} 條既有快取（零 API 成本）。")
```

（`normalize_cache_with_glossary` 已於 2a 於檔頭 import。）

- [ ] **Step 3: config.py 加欄位**

`LanguageConfig`（config.py:82-87）加兩個欄位：

```python
class LanguageConfig(BaseModel):
    code: str
    display_name: str
    system_prompt: str
    # 官方用語庫對照表路徑（相對專案根）；None/空字串＝停用
    glossary_path: str | None = None
    # 模組名譯名對照表路徑；None＝停用
    modnames_glossary_path: str | None = None
    # 使用者自訂用語檔路徑；None＝停用
    custom_glossary_path: str | None = None
```

- [ ] **Step 4: 驗證**

Run: `uv run pytest tests/ -v --timeout=120 -x -q` （全套件）
Expected: 全部 PASS

Run: `uv run python -c "from modpack_translator.gui import worker; from modpack_translator.config import LanguageConfig; print(LanguageConfig(code='zh_tw', display_name='x', system_prompt='y').modnames_glossary_path)"`
Expected: 輸出 `None`，無 ImportError

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/gui/worker.py scripts/translate_modpack.py src/modpack_translator/config.py
git commit -m "feat: worker/CLI 接線三層用語庫與快取正規化"
```

---

### Task 10: GUI 選項區重排＋模組名 checkbox＋設定

**Files:**
- Modify: `src/modpack_translator/gui/main_window.py`（選項群組 :393-457、`_load_remote_settings` :561-590、`_save_remote_settings` :592-602、`_build_cfg` :850-856、`_on_scan` :876-895）

**Interfaces:**
- Consumes: Task 2 `modnames_glossary_path`/`default_custom_glossary_path`/`load_merged_glossary`、Task 9 `ScanWorker(glossary=...)`
- Produces: `self.chk_modnames: QCheckBox`、`self.custom_glossary_btn: QPushButton`、`MainWindow._glossary_for_pipeline() -> Glossary | None`（Task 11 的按鈕 handler 也在此接上）

- [ ] **Step 1: 三列併兩列**

刪除獨立的 `retry_row`（:417-430），把重試控件併入 `checkbox_row`（:397-415 尾端 `addStretch()` 之前）：

```python
        checkbox_row.addSpacing(16)
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 10)
        self.retry_spin.setValue(3)
        self.retry_spin.setFixedWidth(90)
        retry_help = _make_help_label(
            "當後處理器偵測到佔位符遺失時，自動重試翻譯的次數。\n"
            "適用於含有 {0}、%1$s 等格式代碼的字串。\n"
            "0 = 不重試，直接以原文回退並記錄至 Failed Items/。"
        )
        checkbox_row.addWidget(QLabel("重試次數："))
        checkbox_row.addWidget(self.retry_spin)
        checkbox_row.addWidget(retry_help)
        checkbox_row.addStretch()
```

`opt_vbox.addLayout(retry_row)`（:454）刪除，只留 `checkbox_row` 與 `glossary_row` 兩列。

- [ ] **Step 2: glossary_row 加模組名 checkbox 與自訂用語按鈕**

`glossary_help`（:441-447）改為（作廢「手動刪除快取」建議）：

```python
        glossary_help = _make_help_label(
            "把 Minecraft 官方繁中譯名（地獄、界伏蚌、終界…）注入翻譯提示，\n"
            "讓譯文用語與官方一致；整串正好是官方詞彙時直接套用官方譯名，\n"
            "不呼叫模型（省時省費用）。版本對應官方語言檔的 Minecraft 版本。\n"
            "既有快取與既有譯文會在下次翻譯時自動依用語庫修正（零 API 成本），\n"
            "無需刪除快取。"
        )
```

`glossary_row.addStretch()`（:451）之前插入：

```python
        glossary_row.addSpacing(16)
        self.chk_modnames = QCheckBox("模組名譯名")
        self.chk_modnames.setChecked(True)
        self.chk_modnames.toggled.connect(self._save_remote_settings)
        modnames_help = _make_help_label(
            "把常見模組名的通行繁中譯名（暮光森林、機械動力…）納入用語庫，\n"
            "模組名不再被當成專有名詞保留英文。\n"
            "可用「自訂用語」補充冷門模組或覆蓋預建譯名。"
        )
        self.custom_glossary_btn = QPushButton("自訂用語…")
        self.custom_glossary_btn.setFixedWidth(110)
        self.custom_glossary_btn.clicked.connect(self._open_custom_glossary)
        glossary_row.addWidget(self.chk_modnames)
        glossary_row.addWidget(modnames_help)
        glossary_row.addWidget(self.custom_glossary_btn)
```

- [ ] **Step 3: 設定持久化與 cfg 接線**

`_load_remote_settings` 讀值區加：

```python
        modnames_on = str(self._settings.value("options/modnames_enabled", "1")) not in ("0", "false")
```

寫欄位區（`self._loading_settings = True` 的 try 內）加：

```python
            self.chk_modnames.setChecked(modnames_on)
```

`_save_remote_settings` 加：

```python
        self._settings.setValue(
            "options/modnames_enabled", "1" if self.chk_modnames.isChecked() else "0"
        )
```

`_build_cfg` 的官方用語庫段（:850-854）之後加：

```python
        from modpack_translator.pipeline.glossary import (
            default_custom_glossary_path, modnames_glossary_path,
        )
        cfg.language.modnames_glossary_path = None
        if self.chk_modnames.isChecked():
            mp = modnames_glossary_path(cfg.language.code)
            if mp.exists():
                cfg.language.modnames_glossary_path = str(mp)
        cfg.language.custom_glossary_path = str(default_custom_glossary_path())
```

（import 移到檔頭與既有 `available_glossaries` 的 import 併在一起。）

新增方法（放 `_build_cfg` 附近）：

```python
    def _glossary_for_pipeline(self):
        """掃描與翻譯必須用同一套合併用語庫，否則掃描會漏掉只含
        「命中詞英文標題」的檔案（守門讓它們變成待翻項）。"""
        from modpack_translator.pipeline.glossary import (
            default_custom_glossary_path, load_merged_glossary, modnames_glossary_path,
        )
        official = self.glossary_combo.currentData()
        official = official if official and official != "off" else None
        lang_code = self._cfg.language.code if self._cfg else "zh_tw"
        mp = modnames_glossary_path(lang_code)
        modnames = str(mp) if self.chk_modnames.isChecked() and mp.exists() else None
        return load_merged_glossary(official, modnames, str(default_custom_glossary_path()))
```

`_on_scan` 建立 `ScanWorker` 處加傳 `glossary=self._glossary_for_pipeline()`。

暫時加一個 stub（Task 11 實作真身）：

```python
    def _open_custom_glossary(self):
        pass  # Task 11 接上 CustomGlossaryDialog
```

- [ ] **Step 4: 驗證（手動煙霧測試）**

Run: `uv run python main.py`
Expected: 主視窗開啟；「選項」群組只有兩列（第一列：翻譯模組/翻譯任務書/重試次數；第二列：官方用語庫/模組名譯名/自訂用語…）；視窗預設大小下無欄位被壓縮；勾選狀態重啟後保留。關閉視窗。

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/gui/main_window.py
git commit -m "feat: GUI 選項區三列併兩列，加模組名譯名開關（淨高度減一列）"
```

---

### Task 11: 自訂用語表格編輯對話框

**Files:**
- Create: `src/modpack_translator/gui/glossary_dialog.py`
- Modify: `src/modpack_translator/gui/main_window.py`（`_open_custom_glossary` stub 換成實作）

**Interfaces:**
- Consumes: Task 2 `load_custom_terms`/`save_custom_terms`/`default_custom_glossary_path`
- Produces: `CustomGlossaryDialog(QDialog)`；JSON IO 已在 Task 2 測試，對話框本身不做自動化測試（headless 環境）

- [ ] **Step 1: 建立對話框**

```python
# src/modpack_translator/gui/glossary_dialog.py
"""自訂用語編輯器：使用者級 custom_glossary.json 的表格編輯介面。
底層就是一份可手動編輯的 JSON（Path.home()/.modpack_translator/），
表格只是它的編輯介面。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from modpack_translator.pipeline.glossary import (
    default_custom_glossary_path,
    load_custom_terms,
    save_custom_terms,
)


class CustomGlossaryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自訂用語")
        self.resize(560, 440)

        vbox = QVBoxLayout(self)
        hint = QLabel(
            "英文詞 → 繁中譯名，優先序最高（可覆蓋官方用語庫與模組名譯名）。\n"
            "譯名留空 ＝ 保留英文（停用該詞條，可用來壓掉不想要的預建譯名）。\n"
            "詞彙對應請放這裡；題材/語氣描述請放「翻譯語境」。"
        )
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["英文原文", "繁中譯名"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        vbox.addWidget(self.table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("新增列")
        add_btn.clicked.connect(lambda: self._append_row())
        del_btn = QPushButton("刪除選取列")
        del_btn.clicked.connect(self._delete_selected_rows)
        save_btn = QPushButton("儲存")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        vbox.addLayout(btn_row)

        for en, zh in load_custom_terms(default_custom_glossary_path()).items():
            self._append_row(en, zh)

    def _append_row(self, en: str = "", zh: str = "") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(en))
        self.table.setItem(row, 1, QTableWidgetItem(zh))

    def _delete_selected_rows(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def _save(self) -> None:
        terms: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            en_item = self.table.item(row, 0)
            zh_item = self.table.item(row, 1)
            en = (en_item.text() if en_item else "").strip()
            zh = (zh_item.text() if zh_item else "").strip()
            if en:
                terms[en] = zh
        save_custom_terms(default_custom_glossary_path(), terms)
        self.accept()
```

- [ ] **Step 2: 接上主視窗**

`main_window.py` 的 `_open_custom_glossary` stub 換成：

```python
    def _open_custom_glossary(self):
        from modpack_translator.gui.glossary_dialog import CustomGlossaryDialog

        CustomGlossaryDialog(self).exec()
```

- [ ] **Step 3: 驗證（手動煙霧測試）**

Run: `uv run python main.py`
Expected: 點「自訂用語…」開對話框；新增一列 `PokeRod` → `寶可釣竿`，儲存；確認 `%USERPROFILE%\.modpack_translator\custom_glossary.json` 內容正確；重開對話框看到該列；刪除該列儲存後檔案為 `{}`。

- [ ] **Step 4: Commit**

```bash
git add src/modpack_translator/gui/glossary_dialog.py src/modpack_translator/gui/main_window.py
git commit -m "feat: 自訂用語表格編輯對話框"
```

---

### Task 12: 全量驗證與發行版同步

**Files:**
- 無新增；驗證與部署

- [ ] **Step 1: 全套件測試**

Run: `uv run pytest tests/ -q`
Expected: 全部 PASS，無跳過的既有測試

- [ ] **Step 2: 端到端煙霧測試（使用者實測包，遠端模式勿啟用——用掃描驗證即可）**

Run: `uv run python main.py`，模組包路徑填 `C:\Users\user\AppData\Roaming\PrismLauncher\instances\All the Mons-1.0.1\minecraft`，按「掃描模組包」。
Expected: 掃描結果的待翻條數比先前多（守門讓 `Building Gadgets` 等 149 條英文標題重新入列）；log 無錯誤。**不要按開始翻譯**（那是使用者付費 API 的決定）。

- [ ] **Step 3: 同步 Downloads 執行版**

使用者實際執行的是 `C:\Users\user\Downloads\Modpack_Translator`（src 發行版）：

```powershell
robocopy C:\myspace\Modpack_Translator\src C:\Users\user\Downloads\Modpack_Translator\src /E
robocopy C:\myspace\Modpack_Translator\assets\glossary C:\Users\user\Downloads\Modpack_Translator\assets\glossary /E
```

Expected: robocopy 結束碼 ≤ 3（有複製即成功）。

- [ ] **Step 4: Commit（如有殘餘變更）並回報**

```bash
git status --short
```

回報使用者：功能完成、測試結果、掃描煙霧測試觀察到的待翻條數變化、下次翻譯時的預期行為（快取正規化條數會在 log 顯示）。

---

## Self-Review 紀錄

- Spec 覆蓋：§1 資料層（Task 2、3、9 config）、§2 守門三入口（Task 4、5、6、8）、§3 enforce（Task 1、5、6、8）、§4 GUI（Task 10、11）、§5 測試（各 task 內嵌）、§6 遷移（Task 6、7、9、help 文案 Task 10）、§7 介面約定（本計畫不動 prompt 組裝順序，[Glossary] 仍為尾端動態區塊——相容）。
- 型別一致：`_enforce_glossary(glossary, source, translated)` 在 Task 5 定義、Task 6/8 消費，簽名一致；`load_merged_glossary(official, modnames, custom)` 參數順序在 Task 2 定義、Task 9/10 消費一致。
- 已知取捨：`translate_dict` 既有譯文遷移不計入統計（零成本順帶修復）；`ScanWorker` 與翻譯共用 `_glossary_for_pipeline()` 確保掃描/翻譯一致。

## 驗證修正紀錄（2026-07-03，8-agent 對照真實碼 + 心智執行內嵌測試）

- **enforce 空白**（blocker，修正 Task 1 Step 3）：surgical 替換保留 CJK 與英文詞間的空白，
  原 4 個「無空白期望」測試（Task 1/5/6/8）會 FAIL。改 `_enforce_compiled` pattern 把「CJK＋單一
  半形空格」納入 match 一併吃掉，詞本體置 group(1)、`_sub` 以 `terms.get(group(1))` 直取譯名；
  刪除不再需要的 `_lookup_exact_case`。修正後四處測試期望值（無空白）全部成立。
- **CLI 掃描一致性**（major，修正 Task 9）：CLI 自有 `_filter_pending_targets`/`_dry_run_report`
  不帶 glossary、且過濾（:181）早於 glossary 載入（:217），命中詞英文標題會被過濾掉永不修復。
  改為把 glossary 載入移到過濾前、兩函式加 glossary 參數並傳入。
- **死 import**（minor，併入 Task 9 2a）：translate_modpack.py:15 的 `load_glossary` 取代後成為
  未使用 import，改為頂層 import 合併載入所需符號。
- **測試命名**（minor，修正 Task 5）：`test_enforce_token_guard_falls_back` 未實測 fallback，
  改名為 `test_enforce_glossary_applies_and_preserves_tokens` 並加「含 %s token 保留」正向斷言。
- 已確認正確（未改）：preprocessor Task 4 守門與所有內嵌測試、runner 行號與 Task 5/6/7 其餘語義、
  batch_prefill Task 8 守門、三層合併與預建資產、GUI Task 10/11 行號與文案取代字串、
  config 欄位、快取正規化冪等性、無循環匯入。
