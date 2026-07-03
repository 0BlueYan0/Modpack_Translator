# 上下文翻譯 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每包翻譯語境（`<模組包>/.modpack_translator/context.json`）：使用者自訂額外提示詞＋自動累積的動態用語庫（injection-only），加上智慧分批讓同任務字串同批（近零成本鄰近語境）。

**Architecture:** 新模組 `pipeline/pack_context.py` 管每包記憶；`glossary.py` 加多庫合併注入（主用語庫優先、動態層墊底）；translators 掛 `pack_context` 屬性、翻譯成功時記錄學習譯法；`batch_prefill._build_batches` 改語義分組。額外提示詞在建 translator 前併入 system_prompt 靜態段（`[Glossary]` 動態區塊永遠最後，prompt cache 前綴穩定）。

**Tech Stack:** Python 3.11+、pytest、PySide6。無新依賴。

**Spec:** `docs/superpowers/specs/2026-07-03-context-translation-design.md`

**前置：** 先完成《模組名譯名與自訂用語庫》計畫（`2026-07-03-modname-glossary.md`）——本計畫的動態層依賴其合併機制與既有 `Glossary`。

## Global Constraints

- 動態用語庫（learned_terms）**只**參與 prompt 注入；不參與 `enforce`、不參與守門、不參與 `exact_match` 短路——實作上是獨立的第二個 Glossary 實例，絕不併入 `translator.glossary`。
- 注入優先序：主用語庫（自訂>模組名>官方）先佔詞數上限，動態層墊底。
- prompt 組裝順序（cache 約束）：`system prompt（靜態）→ 額外提示詞（整輪不變）→ [Glossary]（逐請求動態，永遠最後）`。
- 智慧分批：同組（`quest.HEX` 前綴或同檔區塊）不拆批；一批可含多組（含跨檔），維持填充率；目標大小 = 設定值（預設 12），硬上限 = 4/3 倍；超大組拆組。
- `context.json` 缺檔/壞檔視為空記憶不報錯；翻譯結束（含取消）寫回。
- GUI 主視窗只新增一個按鈕（翻譯語境…），放第一列尾端，不增加高度。
- 註解/文案繁體中文；測試指令 `uv run pytest tests/<file> -v`。

---

### Task 1: `pipeline/pack_context.py` 每包記憶

**Files:**
- Create: `src/modpack_translator/pipeline/pack_context.py`
- Test: `tests/test_pack_context.py`（新檔）

**Interfaces:**
- Produces:
  - `load_pack_context(game_root: str | Path) -> PackContext`（缺檔/壞檔回空記憶）
  - `PackContext.extra_prompt: str`（屬性，可直接賦值）
  - `PackContext.maybe_record(source: str, translation: str, main_glossary) -> bool`（執行緒安全）
  - `PackContext.learned_glossary() -> Glossary | None`（injection-only 快照，dirty 時才重建）
  - `PackContext.learned_count() -> int`
  - `PackContext.save() -> None`（寫 `<root>/.modpack_translator/context.json`）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_pack_context.py
from __future__ import annotations

from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.pack_context import PackContext, load_pack_context


def test_missing_file_gives_empty_context(tmp_path):
    ctx = load_pack_context(tmp_path)
    assert ctx.extra_prompt == ""
    assert ctx.learned_glossary() is None
    assert ctx.learned_count() == 0


def test_corrupt_file_treated_as_empty(tmp_path):
    d = tmp_path / ".modpack_translator"
    d.mkdir()
    (d / "context.json").write_text("not json{{", encoding="utf-8")
    ctx = load_pack_context(tmp_path)
    assert ctx.extra_prompt == ""
    assert ctx.learned_glossary() is None


def test_roundtrip(tmp_path):
    ctx = load_pack_context(tmp_path)
    ctx.extra_prompt = "這是寶可夢主題包，語氣輕鬆"
    assert ctx.maybe_record("Starlight Sanctum", "星輝聖所", None) is True
    ctx.save()
    ctx2 = load_pack_context(tmp_path)
    assert ctx2.extra_prompt == "這是寶可夢主題包，語氣輕鬆"
    assert ctx2.learned_glossary().terms == {"Starlight Sanctum": "星輝聖所"}


def test_record_conditions():
    ctx = PackContext(root=".")
    # 非專有名詞式短語（小寫句子）不記
    assert ctx.maybe_record("go to the sanctum", "前往聖所", None) is False
    # 譯文無 CJK 不記
    assert ctx.maybe_record("Starlight Sanctum", "Sanctum", None) is False
    # 與原文相同不記
    assert ctx.maybe_record("Starlight Sanctum", "Starlight Sanctum", None) is False
    # 已被主用語庫涵蓋不記
    main = Glossary({"Starlight Sanctum": "星輝聖所"})
    assert ctx.maybe_record("Starlight Sanctum", "別的譯法", main) is False
    # 合格才記
    assert ctx.maybe_record("Starlight Sanctum", "星輝聖所", None) is True
    # 重複記錄回 False
    assert ctx.maybe_record("Starlight Sanctum", "星輝聖所", None) is False
    assert ctx.learned_count() == 1


def test_snapshot_rebuilds_after_record():
    ctx = PackContext(root=".")
    assert ctx.learned_glossary() is None
    ctx.maybe_record("Starlight Sanctum", "星輝聖所", None)
    g1 = ctx.learned_glossary()
    assert g1.terms == {"Starlight Sanctum": "星輝聖所"}
    assert ctx.learned_glossary() is g1  # 未變動時重用快照（避免每請求重編 regex）
    ctx.maybe_record("Moonlit Grove", "月光林地", None)
    assert ctx.learned_glossary().terms["Moonlit Grove"] == "月光林地"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_pack_context.py -v`
Expected: FAIL，`ModuleNotFoundError: modpack_translator.pipeline.pack_context`

- [ ] **Step 3: 實作**

```python
# src/modpack_translator/pipeline/pack_context.py
"""每包翻譯語境：<模組包遊戲根目錄>/.modpack_translator/context.json。

存兩樣東西：
- extra_prompt：使用者描述此包題材/語氣的額外提示詞（GUI 編輯，併入
  system prompt 靜態段）。
- learned_terms：翻譯過程自動累積的 en→zh 譯法（動態用語庫）。權限受限：
  只參與 prompt 注入，不參與 enforce/守門/exact_match——機器自學的譯法
  若一開始就錯，給強制力會把錯誤確定性地擴散到整包。

存放在包資料夾內（與 mods_bak/、quests_bak/ 同慣例）：一包一份、
跟著包走、缺檔壞檔視為空記憶。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.preprocessor import (
    _has_cjk_text,
    _looks_like_proper_noun_phrase,
)

_CONTEXT_RELPATH = Path(".modpack_translator") / "context.json"


class PackContext:
    def __init__(
        self,
        root: str | Path,
        extra_prompt: str = "",
        learned_terms: dict[str, str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.extra_prompt = extra_prompt
        self._terms: dict[str, str] = dict(learned_terms or {})
        self._lock = threading.Lock()
        self._snapshot: Glossary | None = None
        self._snapshot_stale = True

    @property
    def path(self) -> Path:
        return self.root / _CONTEXT_RELPATH

    def maybe_record(
        self, source: str, translation: str, main_glossary: Glossary | None
    ) -> bool:
        """整串成功翻譯時嘗試記錄 en→zh。只記「整串對整串」配對，
        不做句中對齊（會猜錯）。條件全過才記錄，回傳是否有新增/變更。"""
        en = source.strip()
        zh = translation.strip()
        if not en or not zh or en == zh:
            return False
        if not _looks_like_proper_noun_phrase(en):
            return False
        if not _has_cjk_text(zh):
            return False
        if main_glossary is not None and main_glossary.exact_match(en) is not None:
            return False
        with self._lock:
            if self._terms.get(en) == zh:
                return False
            self._terms[en] = zh
            self._snapshot_stale = True
        return True

    def learned_glossary(self) -> Glossary | None:
        """injection-only 的動態 Glossary 快照；無條目時 None。
        只在有新記錄時重建（Glossary 的 regex 編譯不便宜，不能每請求重來）。"""
        with self._lock:
            if self._snapshot_stale:
                self._snapshot = Glossary(self._terms) if self._terms else None
                self._snapshot_stale = False
            return self._snapshot

    def learned_count(self) -> int:
        with self._lock:
            return len(self._terms)

    def save(self) -> None:
        with self._lock:
            payload = {"extra_prompt": self.extra_prompt, "learned_terms": dict(self._terms)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def load_pack_context(game_root: str | Path) -> PackContext:
    """讀取包記憶。缺檔、壞檔、欄位型別不符都視為空記憶，不報錯。"""
    root = Path(game_root)
    try:
        data = json.loads((root / _CONTEXT_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PackContext(root)
    if not isinstance(data, dict):
        return PackContext(root)
    extra = data.get("extra_prompt")
    terms_raw = data.get("learned_terms")
    terms = {
        en: zh
        for en, zh in (terms_raw.items() if isinstance(terms_raw, dict) else ())
        if isinstance(en, str) and isinstance(zh, str) and en.strip() and zh.strip()
    }
    return PackContext(
        root,
        extra_prompt=extra if isinstance(extra, str) else "",
        learned_terms=terms,
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_pack_context.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/pack_context.py tests/test_pack_context.py
git commit -m "feat: 每包翻譯語境 pack_context（extra_prompt＋learned_terms）"
```

---

### Task 2: 多庫合併注入（glossary.py）

**Files:**
- Modify: `src/modpack_translator/pipeline/glossary.py`（`format_block` 抽成模組函式、`augment_prompt` 加 `context_glossary`、新增 `merged_match_pairs`）
- Test: `tests/test_glossary_multi_inject.py`（新檔）

**Interfaces:**
- Produces:
  - `merged_match_pairs(glossaries: Sequence[Glossary | None], texts: Iterable[str]) -> list[tuple[str, str]]`（先者優先、去重）
  - `format_block(pairs, cap=_SINGLE_TERM_CAP) -> str`（模組函式；`Glossary.format_block` 委派之，既有呼叫不變）
  - `augment_prompt(system_prompt, glossary, texts, cap=_SINGLE_TERM_CAP, context_glossary=None) -> str`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_glossary_multi_inject.py
from __future__ import annotations

from modpack_translator.pipeline.glossary import (
    Glossary,
    augment_prompt,
    merged_match_pairs,
)


def test_main_glossary_wins_on_conflict():
    main = Glossary({"Nether": "地獄"})
    dyn = Glossary({"Nether": "下界", "Starlight Sanctum": "星輝聖所"})
    out = augment_prompt(
        "SYS", main, ["Go to Nether near Starlight Sanctum"], context_glossary=dyn
    )
    assert "Nether = 地獄" in out
    assert "Starlight Sanctum = 星輝聖所" in out
    assert "下界" not in out
    assert out.startswith("SYS")  # [Glossary] 永遠附加在尾端


def test_context_glossary_alone_injects():
    dyn = Glossary({"Starlight Sanctum": "星輝聖所"})
    out = augment_prompt("SYS", None, ["Starlight Sanctum ahead"], context_glossary=dyn)
    assert "Starlight Sanctum = 星輝聖所" in out


def test_no_hit_returns_prompt_unchanged():
    dyn = Glossary({"Starlight Sanctum": "星輝聖所"})
    assert augment_prompt("SYS", None, ["hello"], context_glossary=dyn) == "SYS"
    assert augment_prompt("SYS", None, ["hello"]) == "SYS"


def test_main_pairs_fill_cap_before_dynamic():
    main = Glossary({f"Main Term {i}": f"主{i}" for i in range(10)})
    dyn = Glossary({"Dyn Term": "動態"})
    text = " ".join(main.terms) + " Dyn Term"
    pairs = merged_match_pairs((main, dyn), [text])
    # 主用語庫的命中排在前面（cap 截斷時動態層先被丟）
    assert pairs[-1] == ("Dyn Term", "動態")
    assert len(pairs) == 11
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_glossary_multi_inject.py -v`
Expected: FAIL，`ImportError: cannot import name 'merged_match_pairs'`

- [ ] **Step 3: 實作**

`glossary.py`：`Glossary.format_block` 方法本體抽成模組函式（放在 `augment_prompt` 之前），方法委派：

```python
def format_block(pairs: list[tuple[str, str]], cap: int = _SINGLE_TERM_CAP) -> str:
    """把命中的詞彙渲染成附加在 system prompt 尾端的 [Glossary] 區塊。"""
    if not pairs:
        return ""
    lines: list[str] = []
    size = len(_BLOCK_HEADER)
    for en, zh in pairs[:cap]:
        line = f"{en} = {zh}"
        if size + len(line) + 1 > _BLOCK_CHAR_BUDGET:
            break
        lines.append(line)
        size += len(line) + 1
    if not lines:
        return ""
    return _BLOCK_HEADER + "\n".join(lines)


def merged_match_pairs(
    glossaries: Sequence["Glossary | None"],
    texts: Iterable[str],
) -> list[tuple[str, str]]:
    """依序合併多個用語庫的命中結果：先者優先（主用語庫先佔詞數上限、
    衝突時先者勝），大小寫不敏感去重。texts 會被重複掃描，先物化。"""
    materialized = list(texts)
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for g in glossaries:
        if g is None:
            continue
        for en, zh in g.match_terms(materialized):
            if en.lower() in seen:
                continue
            seen.add(en.lower())
            pairs.append((en, zh))
    return pairs
```

`Glossary.format_block` 方法改為委派（保留簽名，既有測試/呼叫不變）：

```python
    def format_block(self, pairs: list[tuple[str, str]], cap: int = _SINGLE_TERM_CAP) -> str:
        """把命中的詞彙渲染成附加在 system prompt 尾端的 [Glossary] 區塊。"""
        return format_block(pairs, cap=cap)
```

`augment_prompt` 改為：

```python
def augment_prompt(
    system_prompt: str,
    glossary: Glossary | None,
    texts: Iterable[str],
    cap: int = _SINGLE_TERM_CAP,
    context_glossary: Glossary | None = None,
) -> str:
    """無用語庫或無命中時原樣回傳 system_prompt，否則附加 [Glossary] 區塊。
    context_glossary（動態層）墊底：主用語庫命中先佔詞數上限。"""
    if glossary is None and context_glossary is None:
        return system_prompt
    pairs = merged_match_pairs((glossary, context_glossary), texts)
    block = format_block(pairs, cap=cap)
    return system_prompt + block
```

檔頭 `from typing import Iterable` 改為 `from typing import Iterable, Sequence`。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_glossary_multi_inject.py tests/test_glossary.py tests/test_glossary_enforce.py tests/test_glossary_merge.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/glossary.py tests/test_glossary_multi_inject.py
git commit -m "feat: 多用語庫合併注入（主用語庫優先、動態層墊底）"
```

---

### Task 3: translator 掛載 pack_context 與動態注入

**Files:**
- Modify: `src/modpack_translator/pipeline/translator.py`（`GGUFTranslator.__init__`、`augment_prompt` 呼叫點 :419、`build_translator` def :428 函式體 :429-437、既有 `if TYPE_CHECKING:` 區塊 :18）
- Modify: `src/modpack_translator/pipeline/remote_translator.py`（`RemoteTranslator.__init__` :42-59、`augment_prompt` 呼叫點 :65、新增 TYPE_CHECKING 區塊）
- Modify: `src/modpack_translator/pipeline/batch_prefill.py`（`_RescueTranslator` :425-456、`_process_batch` :374-381、`_RunContext`、`run_prefill`、`prefill_translation_cache`）
- Test: `tests/test_context_injection.py`（新檔）

> **重要更正：** 兩個 translator 類**不在同一檔**。`GGUFTranslator` 在 translator.py，
> `RemoteTranslator` 在 `remote_translator.py:36`（`__init__` :42-59、`augment_prompt` 呼叫 :65、
> `self.glossary` :53）。使用者用付費遠端 API，逐條/分段路徑走 `RemoteTranslator.translate`，
> 若漏掉 remote_translator.py:65 的注入點，動態用語庫會在使用者的主要路徑上靜默失效。

**Interfaces:**
- Consumes: Task 1 `PackContext.learned_glossary()`、Task 2 `augment_prompt(context_glossary=)`/`merged_match_pairs`/`format_block`
- Produces:
  - `build_translator(cfg, system_prompt, glossary=None, pack_context=None)`；translator 實例有 `.pack_context` 公開屬性（runner 以 getattr 取用）
  - `run_prefill(..., pack_context: PackContext | None = None)`、`prefill_translation_cache(..., pack_context=None)`
  - `_RunContext.pack_context: PackContext | None = None`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_context_injection.py
from __future__ import annotations

import threading
import types

from modpack_translator.pipeline import batch_prefill as bp
from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.pack_context import PackContext


def test_rescue_translator_injects_learned_terms(monkeypatch):
    ctx = PackContext(root=".")
    ctx.maybe_record("Starlight Sanctum", "星輝聖所", None)
    captured: dict = {}

    def fake_stream(client, cfg, model, messages, max_tokens, cancel_event, sleep):
        captured["system"] = messages[0]["content"]
        return "ok"

    monkeypatch.setattr(bp, "_stream_with_backoff", fake_stream)
    # cfg 需帶 max_tokens：_RescueTranslator.translate 在組實參時會求值 self._cfg.max_tokens，
    # 早於 fake_stream 被呼叫；傳 None 會先 AttributeError。用最小 stub。
    cfg = types.SimpleNamespace(max_tokens=256)
    tr = bp._RescueTranslator(
        None, cfg, "m", "SYS", threading.Event(), lambda s: None,
        glossary=None, pack_context=ctx,
    )
    tr.translate("Enter the Starlight Sanctum")
    assert "Starlight Sanctum = 星輝聖所" in captured["system"]
    assert captured["system"].startswith("SYS")


def test_process_batch_block_includes_learned_terms(monkeypatch):
    ctx = PackContext(root=".")
    ctx.maybe_record("Starlight Sanctum", "星輝聖所", None)
    captured: dict = {}

    def fake_request(client, cfg, model, system_prompt, batch, max_tokens,
                     cancel_event, sleep, glossary_block=""):
        captured["block"] = glossary_block
        return '[{"id": 0, "text": "進入星輝聖所"}]'

    monkeypatch.setattr(bp, "_request_batch_raw", fake_request)
    item = bp.PrefillItem(source="Enter the Starlight Sanctum", ck="x")
    enc = bp._EncodedItem(item=item, encoded=item.source, tokens=[])
    bp._process_batch(
        None, None, "m", "SYS", [enc], threading.Event(), lambda s: None,
        glossary=None, pack_context=ctx,
    )
    assert "Starlight Sanctum = 星輝聖所" in captured["block"]


def test_build_translator_attaches_pack_context():
    from modpack_translator.config import ModelConfig
    from modpack_translator.pipeline.translator import build_translator

    ctx = PackContext(root=".")
    cfg = ModelConfig(backend_mode="remote", remote_base_url="http://x", remote_model="m")
    tr = build_translator(cfg, "SYS", None, pack_context=ctx)
    assert tr.pack_context is ctx
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_context_injection.py -v`
Expected: FAIL，`TypeError: ... unexpected keyword argument 'pack_context'`

- [ ] **Step 3: 實作 translator.py 與 remote_translator.py**

`GGUFTranslator`（translator.py）與 `RemoteTranslator`（remote_translator.py）**在不同檔**，兩個類的
`__init__` 都要加參數並存屬性（比照既有 `self.glossary` 公開屬性的寫法與註解）：

```python
        pack_context: "PackContext | None" = None,
```
```python
        self.pack_context = pack_context  # public：每包動態語境（injection-only）
```

**兩個 `augment_prompt` 注入點都要改**（缺一個，動態用語庫就在該路徑失效）：
- translator.py:419（`GGUFTranslator.translate`）
- remote_translator.py:65（`RemoteTranslator.translate`）— 使用者的付費遠端逐條/分段路徑走這裡

兩處都改為：

```python
            augment_prompt(
                self._system_prompt, self.glossary, [text],
                context_glossary=(
                    self.pack_context.learned_glossary()
                    if self.pack_context is not None else None
                ),
            ),
```

`build_translator`（def 於 translator.py:428，函式體 :429-437）改為：

```python
def build_translator(
    cfg: ModelConfig,
    system_prompt: str,
    glossary: "Glossary | None" = None,
    pack_context: "PackContext | None" = None,
):
    ...
        return RemoteTranslator(cfg, system_prompt, glossary, pack_context)
    return GGUFTranslator(cfg, system_prompt, glossary, pack_context)
```

型別匯入（`PackContext` 僅型別用途，避免循環）：
- translator.py **已有** `if TYPE_CHECKING:` 區塊（:18，內含 RemoteTranslator），把
  `from modpack_translator.pipeline.pack_context import PackContext` **加進既有區塊**，勿另建重複區塊。
- remote_translator.py 目前**沒有** TYPE_CHECKING 區塊，需新建：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modpack_translator.pipeline.pack_context import PackContext
```

（remote_translator.py 檔頭若已 `from __future__ import annotations`，字串註解 `"PackContext | None"` 執行期不求值，循環無風險——pack_context.py 只依賴 glossary/preprocessor，不回頭匯入 translator。）

- [ ] **Step 4: 實作 batch_prefill.py**

`_RescueTranslator.__init__` 加 `pack_context=None` 參數、存 `self.pack_context = pack_context`（public 註解同前）；`translate` 的 `augment_prompt(...)` 呼叫加 `context_glossary=` 同 translator.py 寫法。

`_process_batch` 簽名加 `pack_context: "PackContext | None" = None`；glossary_block 建構（:379-381）改為：

```python
        from modpack_translator.pipeline.glossary import format_block, merged_match_pairs
        context_glossary = (
            pack_context.learned_glossary() if pack_context is not None else None
        )
        pairs = merged_match_pairs(
            (glossary, context_glossary), (enc.encoded for enc in batch)
        )
        glossary_block = format_block(pairs, cap=_BATCH_TERM_CAP)
```

（import 移到檔頭既有 glossary import 清單。）

`_RunContext` 加欄位 `pack_context: "PackContext | None" = None`；`_run_batches` 內 `pool.submit(_process_batch, ...)` 的呼叫在 `ctx.glossary` 之後追加 `ctx.pack_context`；建立 `_RescueTranslator` 的地方（`_run_rescue_round` 內，搜尋 `_RescueTranslator(`）追加 `pack_context=ctx.pack_context`。

`run_prefill` 與 `prefill_translation_cache` 簽名各加 `pack_context: "PackContext | None" = None`，`_RunContext(...)` 建構與 `run_prefill(...)` 轉呼叫各加 `pack_context=pack_context`。

- [ ] **Step 5: 跑測試確認通過**

Run: `uv run pytest tests/test_context_injection.py tests/test_translator_factory.py tests/test_batch_prefill.py tests/test_remote_translator.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/modpack_translator/pipeline/translator.py src/modpack_translator/pipeline/remote_translator.py src/modpack_translator/pipeline/batch_prefill.py tests/test_context_injection.py
git commit -m "feat: translator/批次管線掛載 pack_context 動態注入"
```

---

### Task 4: learned_terms 記錄接線

**Files:**
- Modify: `src/modpack_translator/pipeline/runner.py`（`translate_dict`、`_process_patchouli` 的翻譯成功分支）
- Modify: `src/modpack_translator/pipeline/batch_prefill.py`（`_settle` :507-518）
- Test: `tests/test_context_recording.py`（新檔）

**Interfaces:**
- Consumes: Task 1 `PackContext.maybe_record`；translator 的 `.pack_context` 屬性（Task 3）
- Produces: 翻譯成功時（模型路徑）自動記錄整串專有名詞式配對；批次路徑在 `_settle` 記錄

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_context_recording.py
from __future__ import annotations

from modpack_translator.pipeline.pack_context import PackContext
from modpack_translator.pipeline.runner import translate_dict


class NamingTranslator:
    """整串返回固定譯名的假 translator。"""

    glossary = None

    def __init__(self, ctx: PackContext, reply: str):
        self.pack_context = ctx
        self._reply = reply

    def translate(self, text, cancel_check=None):
        return self._reply


def test_translate_dict_records_proper_noun_pair():
    ctx = PackContext(root=".")
    tr = NamingTranslator(ctx, "星輝聖所")
    result, *_ = translate_dict({"k": "Starlight Sanctum"}, {}, tr, {})
    assert result == {"k": "星輝聖所"}
    assert ctx.learned_glossary().terms == {"Starlight Sanctum": "星輝聖所"}


def test_translate_dict_does_not_record_sentences():
    ctx = PackContext(root=".")
    tr = NamingTranslator(ctx, "前往星輝聖所並擊敗首領")
    translate_dict({"k": "Go to the sanctum and defeat the boss"}, {}, tr, {})
    assert ctx.learned_glossary() is None


def test_batch_settle_records():
    import threading
    from modpack_translator.pipeline import batch_prefill as bp

    ctx = PackContext(root=".")
    run_ctx = bp._RunContext(
        client=None, cfg=None, model="m", system_prompt="SYS",
        cache={}, stats=bp.PrefillStats(), total=1,
        cancel_event=threading.Event(), cancel_check=None,
        on_progress=None, on_log=None, flush_cache=None,
        sleep=lambda s: None, glossary=None, pack_context=ctx,
    )
    item = bp.PrefillItem(source="Starlight Sanctum", ck="ck1")
    bp._settle(run_ctx, item, "星輝聖所")
    assert ctx.learned_glossary().terms == {"Starlight Sanctum": "星輝聖所"}
    bp._settle(run_ctx, bp.PrefillItem(source="x", ck="ck2"), None)  # 失敗條不爆炸
```

（若 `_RunContext` 的欄位順序與上述關鍵字參數不合，以關鍵字參數建構為準——欄位名以 batch_prefill.py 現檔為準，缺的欄位補上預設值。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_context_recording.py -v`
Expected: FAIL（learned_glossary 為 None / `_RunContext` 無 pack_context 欄位——後者已在 Task 3 加上，此處應為記錄斷言失敗）

- [ ] **Step 3: 實作**

runner.py `translate_dict`：函式開頭已取 `glossary`（前一計畫），再加：

```python
    pack_context = getattr(translator, "pack_context", None)
```

翻譯成功分支（`final, ok = _translate_segmented_text(...)` 之後的 `if ok:`）加記錄：

```python
        if ok:
            result[key] = final
            cache[ck] = final
            n_translated += 1
            if pack_context is not None:
                pack_context.maybe_record(src, final, glossary)
```

`_process_patchouli` 的翻譯成功分支（`final, ok = _translate_patchouli_text(...)` 之後的 `if ok:`）同樣加（函式開頭同樣取 `pack_context = getattr(translator, "pack_context", None)`）：

```python
            if pack_context is not None:
                pack_context.maybe_record(src, final, glossary)
```

batch_prefill.py `_settle` 成功分支（:510-513）加：

```python
    if final is not None:
        ctx.cache[item.ck] = final
        ctx.stats.translated += 1
        ctx.since_flush += 1
        if ctx.pack_context is not None:
            ctx.pack_context.maybe_record(item.source, final, ctx.glossary)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_context_recording.py tests/test_runner_glossary.py tests/test_batch_prefill.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/runner.py src/modpack_translator/pipeline/batch_prefill.py tests/test_context_recording.py
git commit -m "feat: 翻譯成功時自動記錄每包學習譯法（整串專有名詞式配對）"
```

---

### Task 5: 智慧分批

**Files:**
- Modify: `src/modpack_translator/pipeline/batch_prefill.py`（`PrefillItem` :84-88、`collect_prefill_items` :120-164、`_build_batches` :167-187）
- Test: `tests/test_smart_batching.py`（新檔）

**Interfaces:**
- Produces: `PrefillItem.group: str = ""`；`_build_batches(items, batch_size, char_budget)` 語義分組版（簽名不變，round 2 縮半重試呼叫點免改）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_smart_batching.py
from __future__ import annotations

from modpack_translator.pipeline.batch_prefill import PrefillItem, _build_batches


def _mk(i: int, group: str) -> PrefillItem:
    return PrefillItem(source=f"text {i}", ck=f"ck{i}", group=group)


def test_group_never_split_when_it_fits():
    # 10 + 10：第二組裝不進硬上限（12 + 12//3 = 16）→ 各自成批
    items = [_mk(i, "f::quest.AA") for i in range(10)]
    items += [_mk(100 + i, "f::quest.BB") for i in range(10)]
    batches = _build_batches(items, batch_size=12)
    assert len(batches) == 2
    assert {e.item.group for e in batches[0]} == {"f::quest.AA"}
    assert {e.item.group for e in batches[1]} == {"f::quest.BB"}


def test_small_groups_share_batch_across_files():
    # 小組（含跨檔）併批維持填充率
    items = [_mk(1, "f1::quest.AA"), _mk(2, "f1::quest.AA"), _mk(3, "f2::__file__")]
    batches = _build_batches(items, batch_size=12)
    assert len(batches) == 1


def test_oversized_group_splits():
    items = [_mk(i, "f::__file__") for i in range(30)]
    batches = _build_batches(items, batch_size=12)
    assert all(len(b) <= 12 for b in batches)
    assert sum(len(b) for b in batches) == 30


def test_overflow_allowed_to_keep_group_whole():
    # 8 條已在批中，下一組 6 條：8+6=14 ≤ 16 硬上限 → 同批不拆組
    items = [_mk(i, "f::quest.AA") for i in range(8)]
    items += [_mk(100 + i, "f::quest.BB") for i in range(6)]
    batches = _build_batches(items, batch_size=12)
    assert len(batches) == 1
    assert len(batches[0]) == 14


def test_group_id_derivation():
    from modpack_translator.pipeline.batch_prefill import _group_id

    class T:
        source_file = "chapters/foo.snbt"

    assert _group_id(T(), "quest.1A2B3C.title") == "chapters/foo.snbt::quest.1A2B3C"
    assert _group_id(T(), "quest.1A2B3C.quest_desc[3]") == "chapters/foo.snbt::quest.1A2B3C"
    assert _group_id(T(), "item.mymod.thing") == "chapters/foo.snbt::__file__"


def test_interleaved_groups_are_regrouped_by_sort():
    # diff_keys 回傳 set → 同組在收集順序中不相鄰。_build_batches 必須先排序，
    # 否則 groupby 會把每個 item 切成獨立 run，同任務不會同批。
    items = [
        _mk(1, "f::quest.AA"), _mk(2, "f::quest.BB"), _mk(3, "f::quest.AA"),
        _mk(4, "f::quest.BB"), _mk(5, "f::quest.AA"), _mk(6, "f::quest.BB"),
    ]
    batches = _build_batches(items, batch_size=12)
    # 排序後 AA 三條、BB 三條各自聚合；6 條 ≤ 硬上限 → 同批但同組相鄰
    assert len(batches) == 1
    groups = [e.item.group for e in batches[0]]
    assert groups == ["f::quest.AA"] * 3 + ["f::quest.BB"] * 3
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_smart_batching.py -v`
Expected: FAIL，`TypeError: PrefillItem.__init__() got an unexpected keyword argument 'group'`

- [ ] **Step 3: 實作**

`PrefillItem` 加欄位：

```python
@dataclass(frozen=True)
class PrefillItem:
    source: str  # 原始來源字串
    ck: str      # runner.cache_key(source)
    patchouli: bool = False  # 逐條救援時是否走 _translate_patchouli_text 階梯
    group: str = ""          # 語義分組 id（同任務/同檔區塊同批，見 _build_batches）
```

`_PLACEHOLDER_RE` 附近加：

```python
# FTB Quests lang 鍵前綴：quest.1A2B3C.title / .quest_desc[3] 等同屬一個任務
_QUEST_GROUP_RE = re.compile(
    r"^((?:chapter|chapter_group|quest|task|reward|reward_table|loot_crate|file)"
    r"\.[0-9A-Fa-f]+)\."
)


def _group_id(target: TranslationTarget, key: str) -> str:
    """語義分組 id：任務類鍵以「檔案::quest.HEX」為組，其餘以檔案為組。
    同組字串在 _build_batches 保證同批——批次本身就是鄰近語境。"""
    m = _QUEST_GROUP_RE.match(key)
    scope = m.group(1) if m else "__file__"
    return f"{target.source_file}::{scope}"
```

`collect_prefill_items` 的 `items.append(...)` 改為：

```python
            items.append(PrefillItem(
                source=src, ck=ck,
                patchouli=target.format == "patchouli_json",
                group=_group_id(target, key),
            ))
```

`_build_batches` 全文改為（檔頭加 `import itertools`）：

```python
def _build_batches(
    items: list[PrefillItem],
    batch_size: int,
    char_budget: int = _BATCH_CHAR_BUDGET,
) -> list[list[_EncodedItem]]:
    """語義分組分批：同組（同任務/同檔區塊）不拆批——批次本身就是鄰近語境，
    模型一次看到整個任務的標題＋描述。一批可含多組（含跨檔）維持填充率。
    目標大小 batch_size；為容納整組允許溢出至 4/3 倍（硬上限）；
    單組超過硬上限或字元預算時退回逐條裝箱（拆組）。

    先依 group 穩定排序恢復「同組相鄰」前提——collect_prefill_items 以
    diff_keys（回傳 set，迭代順序不保證）走訪，同任務的 title/quest_desc[*]
    在收集順序中並不相鄰；且 round-2 的 failed_multi 依完成順序收集亦非相鄰。
    不排序則 itertools.groupby 會把同組切成多個 run，智慧分批形同失效。
    Python sort 穩定，組內原始相對順序保留。"""
    items = sorted(items, key=lambda it: it.group)
    hard_cap = batch_size + max(1, batch_size // 3)
    batches: list[list[_EncodedItem]] = []
    current: list[_EncodedItem] = []
    current_chars = 0

    def _close() -> None:
        nonlocal current, current_chars
        if current:
            batches.append(current)
            current = []
            current_chars = 0

    for _gid, group_iter in itertools.groupby(items, key=lambda it: it.group):
        group: list[_EncodedItem] = []
        group_chars = 0
        for item in group_iter:
            encoded, tokens = encode(item.source)
            group.append(_EncodedItem(item=item, encoded=encoded, tokens=tokens))
            group_chars += len(encoded)
        if len(group) > hard_cap or group_chars > char_budget:
            # 超大組：逐條裝箱（等同舊行為，組內順序仍相鄰）
            for enc in group:
                if current and (
                    len(current) >= batch_size
                    or current_chars + len(enc.encoded) > char_budget
                ):
                    _close()
                current.append(enc)
                current_chars += len(enc.encoded)
            continue
        if current and (
            len(current) + len(group) > hard_cap
            or current_chars + group_chars > char_budget
            or len(current) >= batch_size
        ):
            _close()
        current.extend(group)
        current_chars += group_chars
    _close()
    return batches
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_smart_batching.py tests/test_batch_prefill.py -v`
Expected: 全部 PASS（既有 `_build_batches` 相關測試若斷言固定批數，依新語義修正測試——同組相鄰時行為只會更聚合，不會超過 char_budget/硬上限）

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/batch_prefill.py tests/test_smart_batching.py
git commit -m "feat: 智慧分批（同任務同批、彈性批次大小、近零成本鄰近語境）"
```

---

### Task 6: worker / CLI 接線（extra_prompt＋記憶存取）

**Files:**
- Modify: `src/modpack_translator/gui/worker.py`（`TranslateWorker.run` :148-277）
- Modify: `scripts/translate_modpack.py`（translator 建立與 prefill 呼叫、結束存檔）

**Interfaces:**
- Consumes: Task 1 `load_pack_context`/`save`、Task 3 `build_translator(pack_context=)`/`prefill_translation_cache(pack_context=)`
- Produces: 額外提示詞併入 system_prompt 靜態段（在 `[Glossary]` 之前）；結束（含取消/例外）寫回 context.json

- [ ] **Step 1: 實作 worker.py**

import 加 `from modpack_translator.pipeline.pack_context import load_pack_context`。

`TranslateWorker.run` 中 `game_root = resolve_game_root(self._modpack_path)` 之後加：

```python
            # 每包語境：extra_prompt 併入 system prompt 靜態段（cache 友善：
            # [Glossary] 動態區塊永遠在其後），learned_terms 供動態注入
            pack_context = load_pack_context(game_root)
            system_prompt = self._cfg.language.system_prompt
            if pack_context.extra_prompt.strip():
                system_prompt = (
                    system_prompt + "\n\n[Pack context]\n" + pack_context.extra_prompt.strip()
                )
                self.log.emit("已載入此包的翻譯語境提示詞。")
            if pack_context.learned_count():
                self.log.emit(f"已載入此包 {pack_context.learned_count()} 條學習譯法。")
```

`build_translator(...)` 呼叫改為：

```python
                translator = build_translator(
                    self._cfg.model, system_prompt, glossary, pack_context
                )
```

`prefill_translation_cache(...)` 呼叫的 `self._cfg.language.system_prompt` 改為 `system_prompt`，並加 `pack_context=pack_context`。

`finally: translator.close()` 區塊加存檔（在 `translator.close()` 之前）：

```python
            finally:
                try:
                    pack_context.save()
                    if pack_context.learned_count():
                        self.log.emit(
                            f"本包已累積 {pack_context.learned_count()} 條學習譯法"
                            "（存於包內 .modpack_translator/context.json）。"
                        )
                except OSError as exc:
                    self.log.emit(f"[警告] 包語境存檔失敗：{exc}")
                translator.close()
                self._translator = None
```

- [ ] **Step 2: 實作 CLI（scripts/translate_modpack.py）**

`game_root` 解析後（搜尋 `resolve_game_root`）加同樣的 `load_pack_context` 與 system_prompt 併入（print 版訊息）；`build_translator(cfg.model, cfg.language.system_prompt, glossary)` 改為 `build_translator(cfg.model, system_prompt, glossary, pack_context)`；`prefill_translation_cache(...)` 的 system_prompt 參數同步改、加 `pack_context=pack_context`。

存檔要放在既有的 `finally: translator.close()`（scripts/translate_modpack.py:303，close 之前），**不要**放在統計輸出前——翻譯迴圈若拋例外（如 TranslatorFatalError 被 re-raise），放在統計前會被跳過、learned_terms 不寫回，與 GUI（finally 存檔）及「含例外寫回」的介面宣稱不一致。比照 GUI 以 try/except OSError 包住避免存檔失敗遮蔽原始例外：

```python
    finally:
        try:
            pack_context.save()
        except OSError as exc:
            print(f"[警告] 包語境存檔失敗：{exc}")
        translator.close()
```

- [ ] **Step 3: 驗證**

Run: `uv run pytest tests/ -q`
Expected: 全部 PASS

Run: `uv run python -c "from modpack_translator.gui.worker import TranslateWorker; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/modpack_translator/gui/worker.py scripts/translate_modpack.py
git commit -m "feat: worker/CLI 接線每包語境（extra_prompt 靜態段併入、記憶存取）"
```

---

### Task 7: GUI 翻譯語境對話框

**Files:**
- Create: `src/modpack_translator/gui/context_dialog.py`
- Modify: `src/modpack_translator/gui/main_window.py`（第一列尾端加按鈕、`_on_modpack_path_changed` 更新按鈕狀態）

**Interfaces:**
- Consumes: Task 1 `load_pack_context`/`PackContext.save`、`scanner.resolve_game_root`（worker.py 既有匯入來源相同）
- Produces: `ContextDialog(game_root: Path, parent=None)`；`self.context_btn: QPushButton`

- [ ] **Step 1: 建立對話框**

```python
# src/modpack_translator/gui/context_dialog.py
"""翻譯語境編輯器：每包 extra_prompt 的編輯介面。
存於 <模組包>/.modpack_translator/context.json，換包自動切換。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from modpack_translator.pipeline.pack_context import load_pack_context


class ContextDialog(QDialog):
    def __init__(self, game_root: Path, parent=None):
        super().__init__(parent)
        self._game_root = game_root
        self.setWindowTitle("翻譯語境")
        self.resize(520, 360)

        vbox = QVBoxLayout(self)
        hint = QLabel(
            "描述這個模組包的題材、語氣、受眾（例：「寶可夢主題整合包，"
            "任務文字口語輕鬆，玩家多為熟悉寶可夢的老玩家」）。\n"
            "會插入翻譯提示詞，只影響這個包。\n"
            "詞彙對應（X 譯為 Y）請改用「自訂用語」——那邊有強制一致與省費機制。"
        )
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        self.edit = QPlainTextEdit()
        self.edit.setPlainText(load_pack_context(game_root).extra_prompt)
        vbox.addWidget(self.edit)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("儲存")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        vbox.addLayout(btn_row)

    def _save(self) -> None:
        # 重新載入再改 extra_prompt：不覆蓋期間累積的 learned_terms
        ctx = load_pack_context(self._game_root)
        ctx.extra_prompt = self.edit.toPlainText().strip()
        ctx.save()
        self.accept()
```

- [ ] **Step 2: 主視窗接線**

`main_window.py` 選項第一列（前一計畫併入重試次數的 `checkbox_row`）`addStretch()` 之前加：

```python
        self.context_btn = QPushButton("翻譯語境…")
        # 用 minimumWidth 而非 fixedWidth：狀態會換成較長的「翻譯語境…（已設定）」，
        # 固定 120px 會截斷末尾的「（已設定）」使狀態指示失效。
        self.context_btn.setMinimumWidth(120)
        self.context_btn.setEnabled(False)
        self.context_btn.clicked.connect(self._open_context_dialog)
        context_help = _make_help_label(
            "描述此包的題材/語氣讓譯文更貼切；並記住此包翻譯過的譯法，\n"
            "下次翻譯沿用（存於包內 .modpack_translator/，跟著包走）。"
        )
        checkbox_row.addWidget(self.context_btn)
        checkbox_row.addWidget(context_help)
```

新增方法：

```python
    def _resolved_game_root(self):
        from modpack_translator.pipeline.scanner import resolve_game_root

        text = self.modpack_edit.text().strip()
        if not text:
            return None
        root = Path(text)
        if not root.is_dir():
            return None
        try:
            return resolve_game_root(root)
        except Exception:
            return None

    def _update_context_btn(self):
        game_root = self._resolved_game_root()
        self.context_btn.setEnabled(game_root is not None)
        label = "翻譯語境…"
        if game_root is not None:
            from modpack_translator.pipeline.pack_context import load_pack_context

            if load_pack_context(game_root).extra_prompt.strip():
                label = "翻譯語境…（已設定）"
        self.context_btn.setText(label)

    def _open_context_dialog(self):
        game_root = self._resolved_game_root()
        if game_root is None:
            return
        from modpack_translator.gui.context_dialog import ContextDialog

        ContextDialog(game_root, self).exec()
        self._update_context_btn()
```

既有 `_on_modpack_path_changed`（modpack_edit.textChanged 已連接）結尾加一行 `self._update_context_btn()`。

- [ ] **Step 3: 驗證（手動煙霧測試）**

Run: `uv run python main.py`
Expected: 未填包路徑時按鈕停用；填入 `C:\Users\user\AppData\Roaming\PrismLauncher\instances\All the Mons-1.0.1\minecraft` 後啟用；輸入語境文字儲存後按鈕變「翻譯語境…（已設定）」；確認包內 `.modpack_translator/context.json` 生成且內容正確；清空文字儲存後標籤還原。

- [ ] **Step 4: Commit**

```bash
git add src/modpack_translator/gui/context_dialog.py src/modpack_translator/gui/main_window.py
git commit -m "feat: 翻譯語境對話框（每包 extra_prompt，換包自動切換）"
```

---

### Task 8: 全量驗證與發行版同步

**Files:**
- 無新增；驗證與部署

- [ ] **Step 1: 全套件測試**

Run: `uv run pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 2: 端到端煙霧測試（掃描即可，不啟動付費翻譯）**

Run: `uv run python main.py`，包路徑填使用者實測包，設定語境文字，掃描。
Expected: log 顯示掃描正常；語境按鈕顯示（已設定）。

- [ ] **Step 3: 同步 Downloads 執行版**

```powershell
robocopy C:\myspace\Modpack_Translator\src C:\Users\user\Downloads\Modpack_Translator\src /E
robocopy C:\myspace\Modpack_Translator\assets\glossary C:\Users\user\Downloads\Modpack_Translator\assets\glossary /E
```

Expected: robocopy 結束碼 ≤ 3。

- [ ] **Step 4: 回報**

回報使用者：兩個功能完成、測試結果、GUI 變化（兩列選項區的最終樣貌）、下次對實測包翻譯時的預期行為（快取正規化條數、149 條標題修復、學習譯法累積）。

---

## Self-Review 紀錄

- Spec 覆蓋：§1 每包記憶（Task 1、6、7）、§2 智慧分批（Task 5）、§3 動態用語庫（Task 1 記錄條件、Task 2 注入優先序、Task 3 掛載、Task 4 記錄接線；權限隔離——動態層從不進 `translator.glossary`，runner 的守門/enforce 取的是 `translator.glossary`，型別層面隔離）、§4 額外提示詞（Task 6 靜態段併入、Task 7 GUI）、§5 GUI（Task 7）、§6 測試（各 task 內嵌）。
- 型別一致：`PackContext.learned_glossary() -> Glossary | None` 在 Task 1 定義、Task 3 消費；`merged_match_pairs((glossary, context_glossary), texts)` 順序＝優先序，Task 2 定義、Task 3 消費；`_build_batches` 簽名不變，round 2 呼叫點免改。
- 已知取捨：併發中批次拿不到剛學的譯法（盡力而為，spec 允許）；`_settle` 由協調者執行緒呼叫（既有無鎖模型），`maybe_record` 自帶鎖對 GUI/CLI 單協調者都安全。

## 驗證修正紀錄（2026-07-03，8-agent 對照真實碼 + 心智執行內嵌測試）

- **RemoteTranslator 檔案位置**（blocker，修正 Task 3）：`RemoteTranslator` 不在 translator.py，
  而在 `remote_translator.py:36`。原計畫誤稱兩類同檔，會導致 (1) build_translator 以 4 個位置參數
  呼叫 3 參數的 `RemoteTranslator.__init__` → TypeError、test_build_translator_attaches_pack_context
  失敗；(2) 漏掉 remote_translator.py:65 的 `augment_prompt` 注入點——使用者用付費遠端 API，主要
  路徑走 RemoteTranslator.translate，動態用語庫會靜默失效。已修正 Files、Step 3（兩檔各自加參數、
  兩個注入點都改、translator.py 用既有 TYPE_CHECKING 區塊、remote_translator.py 新建區塊）、commit。
- **智慧分批同組不相鄰**（major，修正 Task 5）：`diff_keys` 回傳 set，同任務字串在收集順序中
  不相鄰，`itertools.groupby` 會切成多個 run 使智慧分批失效（六個單元測試因用相鄰 items 建構而
  測不出）。已在 `_build_batches` 開頭加 `items = sorted(items, key=lambda it: it.group)`（一併修好
  round-2 failed_multi 非相鄰）、修正錯誤 docstring、新增 `test_interleaved_groups_are_regrouped_by_sort`
  以真正覆蓋此路徑。
- **_RescueTranslator 測試 cfg=None 崩潰**（major，修正 Task 3 Step 1）：`translate` 求值
  `self._cfg.max_tokens` 早於 monkeypatch 生效，cfg=None 會先 AttributeError。改傳
  `types.SimpleNamespace(max_tokens=256)`（加 `import types`）。
- **CLI 存檔位置**（minor，修正 Task 6）：`pack_context.save()` 改放進既有 `finally:`（含例外都寫回），
  與 GUI 及介面宣稱一致。
- **語境按鈕寬度**（minor，修正 Task 7）：`setFixedWidth(120)` 容不下「翻譯語境…（已設定）」，
  改 `setMinimumWidth(120)`。
- 已確認正確（未改）：pack_context Task 1 全部測試、動態層權限隔離不變式（learned_glossary 絕不
  進 translator.glossary）、Task 2 多庫合併注入與向後相容、Task 3/4 batch_prefill 落點與 _RunContext
  關鍵字建構、Task 4 記錄條件、無循環匯入、pyproject pytest 設定、跨計畫執行順序與 augment_prompt 相容。
