# 伺服器同步功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓使用者設定伺服器資料夾（選填），一鍵把「伺服器端才需要的已翻內容」從客戶端模組包同步到伺服器實例。

**Architecture:** 新增純邏輯模組 `pipeline/sync.py`（manifest 讀寫、依 `format` 分類挑伺服器端檔、規劃與執行複製）。翻譯流程在 `gui/worker.py` 主迴圈掛鉤，把伺服器端輸出檔增量寫進客戶端的 `sync_manifest.json`。GUI 新增「伺服器同步」區塊與 `SyncWorker`（QThread）。同步只增不減、覆蓋前備份、動手前先預覽確認。

**Tech Stack:** Python 3、PySide6（Qt）、pytest、標準庫 `filecmp`/`shutil`/`json`/`datetime`。

## Global Constraints

- 語言：所有使用者可見字串、log、註解一律繁體中文（比照現有 codebase）。
- 相依：只用標準庫與現有相依（PySide6、pydantic、yaml），不新增第三方套件。
- `sync.py` 必須是純邏輯、不 import 任何 Qt/GUI，不呼叫 `datetime.now()`（時間戳由呼叫端傳入），以便 pytest 全覆蓋。
- 同步是客戶端→伺服器**單向**：全程不得修改客戶端任何檔。
- **絕不刪除**伺服器端既有檔；覆蓋前必先備份。
- 伺服器端格式定義以此為準（唯一真相）：`ftbq_snbt`、`ftbq_inline_snbt`、`heracles_snbt`、`heracles_inline_snbt`、`bq_lang`、`datapack_json`。`vh_config_json` 及所有其他格式為客戶端、不同步。
- 遊戲根解析一律用 `scanner.resolve_game_root`（客戶端與伺服器都套用）。
- manifest 路徑：`<客戶端遊戲根>/.modpack_translator/sync_manifest.json`。
- 現有測試指令：`uv run pytest -q`；單檔：`uv run pytest tests/test_sync.py -v`。

---

## File Structure

- **Create** `src/modpack_translator/pipeline/sync.py` — 同步核心純邏輯（格式集合、manifest、plan、apply）。
- **Create** `tests/test_sync.py` — sync.py 全行為測試。
- **Modify** `src/modpack_translator/gui/worker.py` — 翻譯主迴圈掛鉤，收集伺服器端輸出並寫 manifest。
- **Modify** `src/modpack_translator/gui/main_window.py` — 「伺服器同步」UI 區塊、`SyncWorker`、預覽對話框、按鈕流程。

---

## Task 1: sync.py 骨架與伺服器端格式分類

**Files:**
- Create: `src/modpack_translator/pipeline/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Produces:
  - `SERVER_SIDE_FORMATS: frozenset[str]`
  - `is_server_side(fmt: str) -> bool`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_sync.py`：

```python
from modpack_translator.pipeline import sync


def test_server_side_formats_membership():
    assert sync.is_server_side("ftbq_snbt")
    assert sync.is_server_side("ftbq_inline_snbt")
    assert sync.is_server_side("heracles_snbt")
    assert sync.is_server_side("heracles_inline_snbt")
    assert sync.is_server_side("bq_lang")
    assert sync.is_server_side("datapack_json")


def test_client_side_formats_excluded():
    for fmt in (
        "json_lang", "legacy_lang", "pack_json_lang", "pack_legacy_lang",
        "patchouli_json", "oracle_mdx", "oracle_meta", "guideme_md",
        "citadel_book_txt", "rct_names", "kubejs_json", "vh_config_json",
    ):
        assert not sync.is_server_side(fmt), fmt
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'modpack_translator.pipeline.sync'`）

- [ ] **Step 3: 寫最小實作**

建立 `src/modpack_translator/pipeline/sync.py`：

```python
"""伺服器同步：把「伺服器端才需要」的已翻檔從客戶端複製到伺服器實例。

背景：翻譯器對整個客戶端實例翻譯。物品名/GUI 等走 translate key 由客戶端
語言檔解析（伺服器不需要）；但任務資料、資料包字面文字是伺服器載入後同步
給客戶端顯示——連專用伺服器時必須讓伺服器那份也翻好才生效。本模組依輸出
格式挑出伺服器端檔，單向複製到伺服器實例（只增不減、覆蓋前備份）。
"""
from __future__ import annotations

# 伺服器端格式（唯一真相）。vh_config_json 經反編譯確認為客戶端載入，不列入。
SERVER_SIDE_FORMATS: frozenset[str] = frozenset({
    "ftbq_snbt",
    "ftbq_inline_snbt",
    "heracles_snbt",
    "heracles_inline_snbt",
    "bq_lang",
    "datapack_json",
})


def is_server_side(fmt: str) -> bool:
    return fmt in SERVER_SIDE_FORMATS
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_sync.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/sync.py tests/test_sync.py
git commit -m "feat(sync): 伺服器端格式分類骨架"
```

---

## Task 2: manifest 讀寫與合併

**Files:**
- Modify: `src/modpack_translator/pipeline/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `SERVER_SIDE_FORMATS`（Task 1）
- Produces:
  - `ManifestEntry`（dataclass，`rel_path: str`、`format: str`）
  - `manifest_path(game_root: Path) -> Path`
  - `load_manifest(game_root: Path) -> list[ManifestEntry]`
  - `merge_manifest(game_root: Path, entries: list[ManifestEntry]) -> None`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_sync.py` 追加（頂部補 `from pathlib import Path`）：

```python
def test_manifest_path(tmp_path):
    assert sync.manifest_path(tmp_path) == tmp_path / ".modpack_translator" / "sync_manifest.json"


def test_load_manifest_missing_returns_empty(tmp_path):
    assert sync.load_manifest(tmp_path) == []


def test_merge_and_load_roundtrip(tmp_path):
    sync.merge_manifest(tmp_path, [
        sync.ManifestEntry("config/ftbquests/quests/a.snbt", "ftbq_inline_snbt"),
    ])
    got = sync.load_manifest(tmp_path)
    assert got == [sync.ManifestEntry("config/ftbquests/quests/a.snbt", "ftbq_inline_snbt")]


def test_merge_is_union_dedup_by_rel_path(tmp_path):
    sync.merge_manifest(tmp_path, [sync.ManifestEntry("a.snbt", "ftbq_snbt")])
    sync.merge_manifest(tmp_path, [
        sync.ManifestEntry("a.snbt", "ftbq_snbt"),          # 重複 → 去重
        sync.ManifestEntry("b.json", "datapack_json"),      # 新增
    ])
    got = {e.rel_path for e in sync.load_manifest(tmp_path)}
    assert got == {"a.snbt", "b.json"}


def test_load_manifest_corrupt_returns_empty(tmp_path):
    p = sync.manifest_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json", encoding="utf-8")
    assert sync.load_manifest(tmp_path) == []
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'ManifestEntry'`）

- [ ] **Step 3: 寫最小實作**

在 `sync.py` 補（頂部 import 加 `import json`、`from dataclasses import dataclass, asdict`、`from pathlib import Path`）：

```python
@dataclass(frozen=True)
class ManifestEntry:
    rel_path: str          # 相對客戶端遊戲根，一律用正斜線
    format: str


def manifest_path(game_root: Path) -> Path:
    return game_root / ".modpack_translator" / "sync_manifest.json"


def load_manifest(game_root: Path) -> list[ManifestEntry]:
    path = manifest_path(game_root)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    entries: list[ManifestEntry] = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("rel_path"), str) \
                and isinstance(item.get("format"), str):
            entries.append(ManifestEntry(item["rel_path"], item["format"]))
    return entries


def merge_manifest(game_root: Path, entries: list[ManifestEntry]) -> None:
    """以 rel_path 為鍵聯集合併後寫回（跨多次翻譯保持完整）。"""
    merged: dict[str, ManifestEntry] = {e.rel_path: e for e in load_manifest(game_root)}
    for e in entries:
        merged[e.rel_path] = e
    path = manifest_path(game_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(e) for e in merged.values()]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_sync.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/sync.py tests/test_sync.py
git commit -m "feat(sync): manifest 讀寫與聯集合併"
```

---

## Task 3: 從掃描結果建 manifest（相容既有已翻實例）

**Files:**
- Modify: `src/modpack_translator/pipeline/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `SERVER_SIDE_FORMATS`、`ManifestEntry`（Task 1、2）；`scanner.TranslationTarget`（既有）
- Produces: `build_manifest_from_targets(targets, game_root: Path) -> list[ManifestEntry]`

**Note:** 伺服器端格式一律 `output_mode="in_place"`，輸出檔即 `target.target_file`（`Path`）。取其相對 `game_root` 的路徑（正斜線）作 rel_path。`target_file` 若為 None 或不在 game_root 底下則略過。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_sync.py` 追加：

```python
from modpack_translator.pipeline.scanner import TranslationTarget


def _mk_target(fmt, target_file):
    return TranslationTarget(
        source_file=target_file, path_in_jar=None, mod_id="x",
        format=fmt, output_mode="in_place", target_file=target_file,
    )


def test_build_manifest_keeps_only_server_side(tmp_path):
    root = tmp_path
    server_file = root / "config" / "ftbquests" / "quests" / "a.snbt"
    client_file = root / "kubejs" / "assets" / "ns" / "lang" / "zh_tw.json"
    targets = [
        _mk_target("ftbq_inline_snbt", server_file),
        _mk_target("kubejs_json", client_file),        # 客戶端 → 濾除
        _mk_target("vh_config_json", root / "config" / "the_vault" / "x.json"),  # 客戶端 → 濾除
    ]
    entries = sync.build_manifest_from_targets(targets, root)
    assert entries == [sync.ManifestEntry("config/ftbquests/quests/a.snbt", "ftbq_inline_snbt")]


def test_build_manifest_skips_target_outside_root(tmp_path):
    outside = tmp_path.parent / "elsewhere" / "a.snbt"
    entries = sync.build_manifest_from_targets(
        [_mk_target("ftbq_snbt", outside)], tmp_path
    )
    assert entries == []
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL（`AttributeError: ... 'build_manifest_from_targets'`）

- [ ] **Step 3: 寫最小實作**

在 `sync.py` 補：

```python
def build_manifest_from_targets(targets, game_root: Path) -> list[ManifestEntry]:
    """從掃描目標挑出伺服器端格式，產生 manifest 條目（供既有已翻實例
    首次同步時即時重建；輸出檔須落在 game_root 底下才收）。"""
    entries: list[ManifestEntry] = []
    for t in targets:
        if not is_server_side(t.format):
            continue
        out = getattr(t, "target_file", None)
        if out is None:
            continue
        try:
            rel = Path(out).resolve().relative_to(Path(game_root).resolve())
        except ValueError:
            continue
        entries.append(ManifestEntry(rel.as_posix(), t.format))
    return entries
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_sync.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/sync.py tests/test_sync.py
git commit -m "feat(sync): 從掃描結果建 manifest（相容既有已翻實例）"
```

---

## Task 4: plan_sync（規劃複製動作，純函式）

**Files:**
- Modify: `src/modpack_translator/pipeline/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `ManifestEntry`、`load_manifest`（Task 2）
- Produces:
  - `SyncItem`（dataclass，`rel_path: str`、`action: str`；action ∈ `"copy"|"overwrite"|"skip"`）
  - `SyncPlan`（dataclass，`items: list[SyncItem]`；property `copies`/`overwrites`/`skips` 回傳各動作清單）
  - `plan_sync(client_root: Path, server_root: Path, manifest: list[ManifestEntry]) -> SyncPlan`

**Note:** 逐 manifest 條目：客戶端來源檔不存在 → 跳過不列入（來源已被刪的舊條目自我修復）；伺服器端無對應檔 → `copy`；有但位元組不同（`filecmp.cmp(..., shallow=False)`）→ `overwrite`；相同 → `skip`。伺服器端 manifest 未涵蓋的檔一律不出現在 plan（絕不刪）。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_sync.py` 追加：

```python
def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_plan_sync_four_cases(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    # 來源檔（客戶端）
    _write(client / "a.snbt", "AAA")   # 伺服器缺 → copy
    _write(client / "b.snbt", "NEW")   # 伺服器不同 → overwrite
    _write(client / "c.snbt", "SAME")  # 相同 → skip
    _write(client / "d.snbt", "X")     # 來源在但 manifest 也列；伺服器缺 → copy
    # 伺服器端既有
    _write(server / "b.snbt", "OLD")
    _write(server / "c.snbt", "SAME")
    _write(server / "extra.snbt", "KEEP")  # manifest 未涵蓋 → 不得出現在 plan
    manifest = [
        sync.ManifestEntry("a.snbt", "ftbq_snbt"),
        sync.ManifestEntry("b.snbt", "ftbq_snbt"),
        sync.ManifestEntry("c.snbt", "ftbq_snbt"),
        sync.ManifestEntry("d.snbt", "ftbq_snbt"),
    ]
    plan = sync.plan_sync(client, server, manifest)
    actions = {i.rel_path: i.action for i in plan.items}
    assert actions == {"a.snbt": "copy", "b.snbt": "overwrite", "c.snbt": "skip", "d.snbt": "copy"}
    assert "extra.snbt" not in actions
    assert {i.rel_path for i in plan.copies} == {"a.snbt", "d.snbt"}
    assert {i.rel_path for i in plan.overwrites} == {"b.snbt"}
    assert {i.rel_path for i in plan.skips} == {"c.snbt"}


def test_plan_sync_skips_missing_source(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    client.mkdir()
    manifest = [sync.ManifestEntry("gone.snbt", "ftbq_snbt")]  # 客戶端無此檔
    plan = sync.plan_sync(client, server, manifest)
    assert plan.items == []
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL（`AttributeError: ... 'plan_sync'`）

- [ ] **Step 3: 寫最小實作**

在 `sync.py` 補（頂部 import 加 `import filecmp`）：

```python
@dataclass(frozen=True)
class SyncItem:
    rel_path: str
    action: str        # "copy" | "overwrite" | "skip"


@dataclass(frozen=True)
class SyncPlan:
    items: list[SyncItem]

    @property
    def copies(self) -> list[SyncItem]:
        return [i for i in self.items if i.action == "copy"]

    @property
    def overwrites(self) -> list[SyncItem]:
        return [i for i in self.items if i.action == "overwrite"]

    @property
    def skips(self) -> list[SyncItem]:
        return [i for i in self.items if i.action == "skip"]


def plan_sync(client_root: Path, server_root: Path, manifest: list[ManifestEntry]) -> SyncPlan:
    items: list[SyncItem] = []
    seen: set[str] = set()
    for entry in manifest:
        rel = entry.rel_path
        if rel in seen:
            continue
        seen.add(rel)
        src = client_root / rel
        if not src.is_file():
            continue  # 來源已不存在：略過（自我修復舊條目）
        dst = server_root / rel
        if not dst.exists():
            items.append(SyncItem(rel, "copy"))
        elif filecmp.cmp(str(src), str(dst), shallow=False):
            items.append(SyncItem(rel, "skip"))
        else:
            items.append(SyncItem(rel, "overwrite"))
    return SyncPlan(items)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_sync.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/sync.py tests/test_sync.py
git commit -m "feat(sync): plan_sync 規劃複製動作"
```

---

## Task 5: apply_sync（執行複製＋備份）

**Files:**
- Modify: `src/modpack_translator/pipeline/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `SyncPlan`、`SyncItem`（Task 4）
- Produces:
  - `SyncResult`（dataclass，`copied: list[str]`、`overwritten: list[str]`、`skipped: list[str]`、`failed: list[tuple[str, str]]`、`backup_dir: Path | None`）
  - `apply_sync(plan, client_root, server_root, backup_dir, on_progress=None) -> SyncResult`

**Note:** `backup_dir` 由呼叫端傳入（含時間戳，維持模組無時間相依）。只有實際 overwrite 才建立 backup_dir 並先把伺服器原檔複製過去（保留相對路徑）。copy/overwrite 用 `shutil.copy2`，自動建缺少父目錄。單檔失敗記入 `failed` 並繼續。`on_progress(done, total)` 選填。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_sync.py` 追加（頂部補 `import shutil` 若需要）：

```python
def test_apply_sync_copies_overwrites_backs_up(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    _write(client / "a.snbt", "AAA")
    _write(client / "b.snbt", "NEW")
    _write(client / "c.snbt", "SAME")
    _write(server / "b.snbt", "OLD")
    _write(server / "c.snbt", "SAME")
    manifest = [
        sync.ManifestEntry("a.snbt", "ftbq_snbt"),
        sync.ManifestEntry("b.snbt", "ftbq_snbt"),
        sync.ManifestEntry("c.snbt", "ftbq_snbt"),
    ]
    plan = sync.plan_sync(client, server, manifest)
    backup = server / ".modpack_translator" / "sync_bak" / "20260720_120000"
    result = sync.apply_sync(plan, client, server, backup)

    # 複製與覆蓋生效
    assert (server / "a.snbt").read_text(encoding="utf-8") == "AAA"
    assert (server / "b.snbt").read_text(encoding="utf-8") == "NEW"
    # 覆蓋前的原檔已備份且可還原
    assert (backup / "b.snbt").read_text(encoding="utf-8") == "OLD"
    # skip 的檔不進備份
    assert not (backup / "c.snbt").exists()
    assert set(result.copied) == {"a.snbt"}
    assert set(result.overwritten) == {"b.snbt"}
    assert set(result.skipped) == {"c.snbt"}
    assert result.backup_dir == backup


def test_apply_sync_no_backup_dir_when_no_overwrite(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    _write(client / "a.snbt", "AAA")
    plan = sync.plan_sync(client, server, [sync.ManifestEntry("a.snbt", "ftbq_snbt")])
    backup = server / ".modpack_translator" / "sync_bak" / "ts"
    sync.apply_sync(plan, client, server, backup)
    assert (server / "a.snbt").read_text(encoding="utf-8") == "AAA"
    assert not backup.exists()   # 沒有 overwrite 就不建備份資料夾


def test_apply_sync_never_deletes_server_extra(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    _write(client / "a.snbt", "AAA")
    _write(server / "extra.snbt", "KEEP")
    plan = sync.plan_sync(client, server, [sync.ManifestEntry("a.snbt", "ftbq_snbt")])
    sync.apply_sync(plan, client, server, server / "bak")
    assert (server / "extra.snbt").read_text(encoding="utf-8") == "KEEP"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL（`AttributeError: ... 'apply_sync'`）

- [ ] **Step 3: 寫最小實作**

在 `sync.py` 補（頂部 import 加 `import shutil`）：

```python
@dataclass
class SyncResult:
    copied: list[str]
    overwritten: list[str]
    skipped: list[str]
    failed: list[tuple[str, str]]      # (rel_path, 錯誤訊息)
    backup_dir: Path | None


def apply_sync(plan, client_root: Path, server_root: Path, backup_dir: Path,
               on_progress=None) -> SyncResult:
    copied: list[str] = []
    overwritten: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    backup_used = False

    actionable = [i for i in plan.items if i.action in ("copy", "overwrite")]
    total = len(actionable)
    done = 0
    for item in plan.items:
        if item.action == "skip":
            skipped.append(item.rel_path)
            continue
        src = client_root / item.rel_path
        dst = server_root / item.rel_path
        try:
            if item.action == "overwrite":
                bak = backup_dir / item.rel_path
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(dst), str(bak))
                backup_used = True
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            (overwritten if item.action == "overwrite" else copied).append(item.rel_path)
        except OSError as exc:
            failed.append((item.rel_path, str(exc)))
        done += 1
        if on_progress is not None:
            on_progress(done, total)
    return SyncResult(copied, overwritten, skipped, failed,
                      backup_dir if backup_used else None)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_sync.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/sync.py tests/test_sync.py
git commit -m "feat(sync): apply_sync 執行複製與覆蓋備份"
```

---

## Task 6: 端到端與 resolve_game_root 整合測試

**Files:**
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: 全部（Task 1–5）；`scanner.resolve_game_root`（既有）

**Note:** 本任務只加測試（驗證各單元組合後的整體行為與伺服器佈局辨識），不改產品碼。若測試揭露缺陷才回頭修對應任務。

- [ ] **Step 1: 寫端到端測試**

在 `tests/test_sync.py` 追加：

```python
from modpack_translator.pipeline.scanner import resolve_game_root


def test_end_to_end_only_server_side_synced(tmp_path):
    # 假客戶端：伺服器端(ftbq + datapack) + 客戶端(mods jar + the_vault)
    client = tmp_path / "client"
    _write(client / "config" / "ftbquests" / "quests" / "ch1.snbt", "任務一")
    _write(client / "kubejs" / "data" / "skilltree" / "skills" / "mage.json", '{"title":"法師"}')
    _write(client / "config" / "the_vault" / "lang" / "zh_tw" / "x.json", '{"a":"甲"}')
    (client / "mods").mkdir()
    (client / "mods" / "x.jar").write_bytes(b"PK\x03\x04zip")
    server = tmp_path / "server"
    server.mkdir()

    targets = [
        _mk_target("ftbq_inline_snbt", client / "config" / "ftbquests" / "quests" / "ch1.snbt"),
        _mk_target("datapack_json", client / "kubejs" / "data" / "skilltree" / "skills" / "mage.json"),
        _mk_target("vh_config_json", client / "config" / "the_vault" / "lang" / "zh_tw" / "x.json"),
    ]
    manifest = sync.build_manifest_from_targets(targets, client)
    plan = sync.plan_sync(client, server, manifest)
    sync.apply_sync(plan, client, server, server / "bak")

    # 伺服器端內容被複製
    assert (server / "config" / "ftbquests" / "quests" / "ch1.snbt").exists()
    assert (server / "kubejs" / "data" / "skilltree" / "skills" / "mage.json").exists()
    # 客戶端內容（the_vault、mods）不被複製
    assert not (server / "config" / "the_vault").exists()
    assert not (server / "mods").exists()


def test_server_root_resolution_layouts(tmp_path):
    # PrismLauncher 式：<instance>/minecraft/
    prism = tmp_path / "inst"
    (prism / "minecraft" / "config").mkdir(parents=True)
    assert resolve_game_root(prism) == prism / "minecraft"
    # 專用伺服器式：config/ 直接在頂層
    ded = tmp_path / "server"
    (ded / "config").mkdir(parents=True)
    assert resolve_game_root(ded) == ded
```

- [ ] **Step 2: 執行測試確認通過**

Run: `uv run pytest tests/test_sync.py -v`
Expected: PASS（全部）。若 `test_end_to_end_only_server_side_synced` 失敗，檢查 Task 3/4/5 對應實作。

- [ ] **Step 3: 全套件回歸**

Run: `uv run pytest -q`
Expected: 全綠（既有 527 + 本檔新測試）

- [ ] **Step 4: Commit**

```bash
git add tests/test_sync.py
git commit -m "test(sync): 端到端與伺服器佈局辨識"
```

---

## Task 7: 翻譯流程掛鉤——增量寫 manifest

**Files:**
- Modify: `src/modpack_translator/gui/worker.py`（`TranslateWorker.run` 主迴圈，約 268–301 行）
- Test: `tests/test_sync_manifest_hook.py`（新建，測純函式；GUI 迴圈本身不做單元測試）

**Interfaces:**
- Consumes: `sync.ManifestEntry`、`sync.is_server_side`、`sync.merge_manifest`、`sync.build_manifest_from_targets`（Task 1–3）
- Produces: `sync.collect_server_side_entries(targets, game_root) -> list[ManifestEntry]`（新增至 sync.py，供 worker 與測試共用；語意等同 `build_manifest_from_targets`，但取名表達「收集本輪成功目標」；為避免重複，直接讓 worker 呼叫既有 `build_manifest_from_targets`，本任務不新增函式，僅在 worker 掛鉤並加一條整合測試）

**Note:** worker 主迴圈對每個 `process_target` 成功的 target，若 `is_server_side(target.format)` 就蒐集；迴圈結束（含正常結束與使用者取消）後把已成功者 `merge_manifest` 進客戶端 game_root。掛鉤不可影響翻譯結果——用 try/except 包住，失敗只 log。

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_sync_manifest_hook.py`：

```python
"""驗證 worker 蒐集成功的伺服器端目標並寫入 manifest 的邏輯（抽出成純函式測）。"""
from pathlib import Path

from modpack_translator.pipeline import sync
from modpack_translator.pipeline.scanner import TranslationTarget


def _t(fmt, target_file):
    return TranslationTarget(
        source_file=target_file, path_in_jar=None, mod_id="x",
        format=fmt, output_mode="in_place", target_file=target_file,
    )


def test_only_server_side_successful_targets_written(tmp_path):
    root = tmp_path
    a = root / "config" / "ftbquests" / "quests" / "a.snbt"
    b = root / "kubejs" / "assets" / "ns" / "lang" / "zh_tw.json"  # 客戶端
    a.parent.mkdir(parents=True, exist_ok=True); a.write_text("x", encoding="utf-8")
    b.parent.mkdir(parents=True, exist_ok=True); b.write_text("x", encoding="utf-8")

    # 模擬 worker：成功目標清單（a 伺服器端、b 客戶端）
    successful = [_t("ftbq_inline_snbt", a), _t("kubejs_json", b)]
    entries = sync.build_manifest_from_targets(successful, root)
    sync.merge_manifest(root, entries)

    got = {e.rel_path for e in sync.load_manifest(root)}
    assert got == {"config/ftbquests/quests/a.snbt"}
```

- [ ] **Step 2: 執行測試確認通過**

Run: `uv run pytest tests/test_sync_manifest_hook.py -v`
Expected: PASS（此測試驗證的是 Task 1–3 既有函式的組合，應直接通過；作為 worker 掛鉤的行為契約）

- [ ] **Step 3: 在 worker 掛鉤**

在 `src/modpack_translator/gui/worker.py` 頂部 import 區（約第 33 行 `from modpack_translator.pipeline.scanner import ...` 附近）加：

```python
from modpack_translator.pipeline import sync as sync_mod
```

在 `TranslateWorker.run` 迴圈前（約第 268 行 `for i, target in enumerate(self._targets):` 之前）加：

```python
                synced_ok: list[TranslationTarget] = []
```

在 `process_target` 成功後（約第 288 行 `if failed:` 區塊之後、`except TranslatorFatalError` 之前）加：

```python
                        if sync_mod.is_server_side(target.format):
                            synced_ok.append(target)
```

在 `_flush_cache(cache_path, cache)`（約第 301 行，迴圈結束後那一次）之後加：

```python
                try:
                    entries = sync_mod.build_manifest_from_targets(synced_ok, game_root)
                    if entries:
                        sync_mod.merge_manifest(game_root, entries)
                        self.log.emit(f"已更新伺服器同步清單（{len(entries)} 個伺服器端檔）。")
                except Exception as exc:  # noqa: BLE001 — manifest 失敗不可影響翻譯
                    self.log.emit(f"[警告] 同步清單更新失敗（不影響翻譯）：{exc}")
```

- [ ] **Step 4: 執行回歸**

Run: `uv run pytest -q`
Expected: 全綠。

- [ ] **Step 5: 手動語法檢查（import 正確）**

Run: `uv run python -c "import modpack_translator.gui.worker"`
Expected: 無錯誤輸出（成功 import）

- [ ] **Step 6: Commit**

```bash
git add src/modpack_translator/gui/worker.py tests/test_sync_manifest_hook.py
git commit -m "feat(sync): 翻譯流程增量寫入 manifest"
```

---

## Task 8: GUI「伺服器同步」區塊與設定持久化

**Files:**
- Modify: `src/modpack_translator/gui/main_window.py`（模組包群組後、約第 227 行 `root_layout.addWidget(modpack_group)` 之後插入；瀏覽方法加在約第 550 行 `_browse_modpack` 附近）

**Interfaces:**
- Consumes: 既有 `self._settings`（QSettings）、`self.modpack_edit`
- Produces: `self.server_dir_edit`（QLineEdit）、`self.sync_btn`（QPushButton）、`_browse_server_dir`、`_on_server_dir_changed`、`_update_sync_btn_enabled`

**Note:** 此任務只做 UI 與設定讀寫＋按鈕啟用狀態，尚未接同步邏輯（下一任務）。UI 手動驗證為主；持久化以既有 QSettings 模式。

- [ ] **Step 1: 新增 UI 區塊**

在 `main_window.py` 約第 227 行 `root_layout.addWidget(modpack_group)` 之後插入：

```python
        # ── 伺服器同步群組 ────────────────────────────────────────────────
        sync_group = QGroupBox("伺服器同步（選填）")
        sf = QFormLayout(sync_group)
        sf.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        server_row = QHBoxLayout()
        self.server_dir_edit = QLineEdit()
        self.server_dir_edit.setPlaceholderText("專用伺服器實例資料夾（不同步可留空）…")
        self.server_dir_edit.setText(self._settings.value("sync/server_dir", "") or "")
        self.server_dir_edit.textChanged.connect(self._on_server_dir_changed)
        _browse_server_btn = QPushButton("瀏覽…")
        _browse_server_btn.setFixedWidth(80)
        _browse_server_btn.clicked.connect(self._browse_server_dir)
        server_row.addWidget(self.server_dir_edit)
        server_row.addWidget(_browse_server_btn)
        sf.addRow("伺服器資料夾：", server_row)

        self.sync_btn = QPushButton("同步到伺服器")
        self.sync_btn.clicked.connect(self._on_sync_clicked)
        sf.addRow("", self.sync_btn)

        root_layout.addWidget(sync_group)
        self._update_sync_btn_enabled()
```

- [ ] **Step 2: 新增瀏覽／設定／啟用方法**

在 `main_window.py` 約第 553 行 `_browse_modpack` 方法之後插入：

```python
    def _browse_server_dir(self):
        path = QFileDialog.getExistingDirectory(self, "選擇專用伺服器實例資料夾")
        if path:
            self.server_dir_edit.setText(path)

    def _on_server_dir_changed(self, text: str):
        self._settings.setValue("sync/server_dir", text.strip())
        self._update_sync_btn_enabled()

    def _update_sync_btn_enabled(self):
        has_server = bool(self.server_dir_edit.text().strip())
        self.sync_btn.setEnabled(has_server)
        self.sync_btn.setToolTip(
            "" if has_server else "請先選擇伺服器資料夾才能同步。"
        )
```

- [ ] **Step 3: 加暫時的 `_on_sync_clicked` 佔位（下一任務替換）**

在 `_update_sync_btn_enabled` 之後插入：

```python
    def _on_sync_clicked(self):
        QMessageBox.information(self, "同步", "同步邏輯將於下一步接上。")
```

- [ ] **Step 4: 語法與 import 檢查**

Run: `uv run python -c "import modpack_translator.gui.main_window"`
Expected: 無錯誤（確認 `QGroupBox`、`QFormLayout`、`QLineEdit`、`QPushButton`、`QHBoxLayout`、`QFileDialog`、`QMessageBox` 皆已在既有 import；本檔已全數 import）

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/gui/main_window.py
git commit -m "feat(sync): GUI 伺服器同步區塊與設定持久化"
```

---

## Task 9: SyncWorker（QThread）與同步流程接線

**Files:**
- Modify: `src/modpack_translator/gui/main_window.py`（替換 `_on_sync_clicked`；檔末加 `SyncWorker` 類別，比照既有 `UpdateCheckWorker` 約第 1297 行的樣式）

**Interfaces:**
- Consumes: `sync`（`load_manifest`/`build_manifest_from_targets`/`plan_sync`/`apply_sync`）、`ModpackScanner`、`resolve_game_root`、`datetime`
- Produces: `SyncWorker(QThread)`（signals：`finished(object)` 傳 `SyncResult`、`error(str)`、`log(str)`）、`_start_sync(client_root, server_root)`、`_on_sync_done`、`_on_sync_error`

**Note:** 預覽在主執行緒先算（plan 很快，不需背景）；使用者確認後才把 `apply_sync` 丟進 `SyncWorker`。時間戳用 `datetime.now().strftime("%Y%m%d_%H%M%S")` 在主執行緒產生後傳入。manifest 缺時即時掃描重建（`include_translated=True`）。

- [ ] **Step 1: 替換 `_on_sync_clicked` 為完整流程**

把 Task 8 的佔位 `_on_sync_clicked` 整段替換為：

```python
    def _on_sync_clicked(self):
        from datetime import datetime
        from modpack_translator.pipeline import sync as sync_mod
        from modpack_translator.pipeline.scanner import ModpackScanner, resolve_game_root

        client_text = self.modpack_edit.text().strip()
        server_text = self.server_dir_edit.text().strip()
        if not client_text:
            QMessageBox.warning(self, "同步", "請先選擇模組包（客戶端）資料夾。")
            return
        if not server_text:
            QMessageBox.warning(self, "同步", "請先選擇伺服器資料夾。")
            return

        client_root = resolve_game_root(Path(client_text))
        server_root = resolve_game_root(Path(server_text))
        if client_root.resolve() == server_root.resolve():
            QMessageBox.warning(self, "同步", "客戶端與伺服器解析後是同一個資料夾，無法同步。")
            return

        # 取得 manifest；缺則即時掃描重建（相容既有已翻實例）
        manifest = sync_mod.load_manifest(client_root)
        if not manifest:
            reply = QMessageBox.question(
                self, "建立同步清單",
                "找不到同步清單（可能是舊版翻譯或尚未翻譯）。\n"
                "要現在掃描客戶端建立清單嗎？（約需數十秒）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            targets = ModpackScanner().scan(client_root, "zh_tw", None, include_translated=True)
            manifest = sync_mod.build_manifest_from_targets(targets, client_root)
            if manifest:
                sync_mod.merge_manifest(client_root, manifest)

        plan = sync_mod.plan_sync(client_root, server_root, manifest)
        n_copy = len(plan.copies)
        n_over = len(plan.overwrites)
        n_skip = len(plan.skips)
        if n_copy == 0 and n_over == 0:
            QMessageBox.information(
                self, "同步",
                "沒有需要同步的伺服器端內容"
                + ("（全部已是最新）。" if n_skip else "。"),
            )
            return

        preview = (
            f"將同步到：{server_root}\n\n"
            f"新增複製：{n_copy} 個\n"
            f"覆蓋（會先備份）：{n_over} 個\n"
            f"略過（已相同）：{n_skip} 個\n\n"
            + "\n".join(f"  + {i.rel_path}" for i in plan.copies[:20])
            + ("\n  …" if n_copy > 20 else "")
            + ("\n" if plan.overwrites else "")
            + "\n".join(f"  ~ {i.rel_path}" for i in plan.overwrites[:20])
            + ("\n  …" if n_over > 20 else "")
            + "\n\n確定要同步嗎？"
        )
        reply = QMessageBox.question(
            self, "確認同步", preview,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = server_root / ".modpack_translator" / "sync_bak" / ts
        self._start_sync(plan, client_root, server_root, backup_dir)

    def _start_sync(self, plan, client_root, server_root, backup_dir):
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("同步中…")
        self._sync_worker = SyncWorker(plan, client_root, server_root, backup_dir)
        self._sync_worker.log.connect(self.log_edit.appendPlainText)
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_sync_error)
        self._sync_worker.start()

    def _on_sync_done(self, result):
        self.sync_btn.setText("同步到伺服器")
        self._update_sync_btn_enabled()
        msg = (
            f"同步完成。\n\n"
            f"新增：{len(result.copied)} 個\n"
            f"覆蓋：{len(result.overwritten)} 個\n"
            f"略過：{len(result.skipped)} 個"
        )
        if result.backup_dir is not None:
            msg += f"\n\n原檔備份於：\n{result.backup_dir}"
        if result.failed:
            msg += f"\n\n⚠ {len(result.failed)} 個檔失敗：\n" + "\n".join(
                f"  {rel}：{err}" for rel, err in result.failed[:10]
            )
        QMessageBox.information(self, "同步", msg)

    def _on_sync_error(self, msg: str):
        self.sync_btn.setText("同步到伺服器")
        self._update_sync_btn_enabled()
        QMessageBox.critical(self, "同步失敗", msg)
```

- [ ] **Step 2: 在檔末新增 `SyncWorker` 類別**

在 `main_window.py` 檔末（既有 `UpdateCheckWorker` 之後）加：

```python
class SyncWorker(QThread):
    finished = Signal(object)      # SyncResult
    error    = Signal(str)
    log      = Signal(str)

    def __init__(self, plan, client_root, server_root, backup_dir):
        super().__init__()
        self._plan = plan
        self._client_root = client_root
        self._server_root = server_root
        self._backup_dir = backup_dir

    def run(self):
        try:
            from modpack_translator.pipeline import sync as sync_mod
            result = sync_mod.apply_sync(
                self._plan, self._client_root, self._server_root, self._backup_dir,
                on_progress=lambda done, total: self.log.emit(f"同步中… {done}/{total}"),
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
```

- [ ] **Step 3: closeEvent 收尾 SyncWorker（避免關閉時執行緒懸掛）**

在 `main_window.py` 的 `closeEvent`（約第 1278 行）內、`event.accept()` 之前加：

```python
        if getattr(self, "_sync_worker", None) and self._sync_worker.isRunning():
            if not self._sync_worker.wait(3_000):
                self._sync_worker.terminate()
                self._sync_worker.wait(1_000)
```

- [ ] **Step 4: 語法與 import 檢查**

Run: `uv run python -c "import modpack_translator.gui.main_window"`
Expected: 無錯誤。

- [ ] **Step 5: 全套件回歸**

Run: `uv run pytest -q`
Expected: 全綠。

- [ ] **Step 6: Commit**

```bash
git add src/modpack_translator/gui/main_window.py
git commit -m "feat(sync): SyncWorker 與同步預覽/執行流程接線"
```

---

## Task 10: 版本號、GUI 手動驗證與同步到 Downloads 執行版

**Files:**
- Modify: `src/modpack_translator/version.py`、`pyproject.toml`、`uv.lock`

**Note:** 依專案慣例，功能完成後 bump 版本並同步到使用者實際執行的 Downloads 發行版（`C:\Users\user\Downloads\Modpack_Translator`）。

- [ ] **Step 1: bump 版本至 1.14.0**

- `src/modpack_translator/version.py`：`__version__ = "1.13.0"` → `"1.14.0"`
- `pyproject.toml`：`version = "1.13.0"` → `"1.14.0"`
- `uv.lock`：`name = "modpack-translator"` 區塊的 `version = "1.13.0"` → `"1.14.0"`

- [ ] **Step 2: 全套件回歸**

Run: `uv run pytest -q`
Expected: 全綠。

- [ ] **Step 3: GUI 手動驗證（人工）**

啟動 GUI（`uv run python main.py` 或既有啟動方式），確認：
- 「伺服器同步（選填）」區塊出現在「模組包」下方。
- 伺服器欄位空白時「同步到伺服器」按鈕為禁用、hover 有提示。
- 填入一個測試伺服器資料夾後按鈕啟用。
- 對已翻譯的 Soulrend 客戶端 + 一個空的假伺服器資料夾按同步：出現「建立清單」詢問 → 掃描 → 預覽（複製 N）→ 確認 → 完成摘要；伺服器端出現 `config/ftbquests`、`config/paxi/datapacks`、`kubejs/data` 的檔，且無 `mods/`、無 `config/the_vault/`。

- [ ] **Step 4: 同步到 Downloads 執行版**

```bash
repo="/c/myspace/Modpack_Translator"; dl="/c/Users/user/Downloads/Modpack_Translator"
cp "$repo/src/modpack_translator/version.py" "$dl/src/modpack_translator/version.py"
cp "$repo/pyproject.toml" "$dl/pyproject.toml"
cp "$repo/uv.lock" "$dl/uv.lock"
cp "$repo/src/modpack_translator/pipeline/sync.py" "$dl/src/modpack_translator/pipeline/sync.py"
cp "$repo/src/modpack_translator/gui/worker.py" "$dl/src/modpack_translator/gui/worker.py"
cp "$repo/src/modpack_translator/gui/main_window.py" "$dl/src/modpack_translator/gui/main_window.py"
```

Run（驗證 Downloads 版可 import）:
```bash
cd "/c/Users/user/Downloads/Modpack_Translator" && python -c "import sys; sys.path.insert(0,'src'); import modpack_translator.pipeline.sync, modpack_translator.gui.main_window; print('Downloads OK')"
```
Expected: `Downloads OK`

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/version.py pyproject.toml uv.lock
git commit -m "chore: v1.14.0 伺服器同步功能"
```

---

## Self-Review

**Spec coverage:**
- 格式分類（含 vh_config_json 排除）→ Task 1 ✓
- manifest 讀寫/合併 → Task 2 ✓
- 相容既有已翻實例（掃描重建）→ Task 3（`build_manifest_from_targets`）＋ Task 9（GUI 缺 manifest 時掃描）✓
- plan_sync 四情況、絕不刪 → Task 4 ✓
- apply_sync 複製/覆蓋/備份 → Task 5 ✓
- resolve_game_root 兩佈局、端到端只同步伺服器端 → Task 6 ✓
- 翻譯流程掛鉤增量寫 manifest → Task 7 ✓
- GUI 區塊/設定/按鈕禁用 → Task 8 ✓
- SyncWorker/預覽/摘要/備份路徑/client==server 擋下 → Task 9 ✓
- 版本 bump + Downloads 同步 → Task 10 ✓

**Placeholder scan:** 各步驟皆含完整程式碼與明確指令，無 TBD/TODO。Task 7 Interfaces 的 `collect_server_side_entries` 已在 Note 中明確裁定「不新增函式、直接用 `build_manifest_from_targets`」，避免引用未定義符號。

**Type consistency:**
- `ManifestEntry(rel_path, format)`、`SyncItem(rel_path, action)`、`SyncPlan.items/copies/overwrites/skips`、`SyncResult(copied, overwritten, skipped, failed, backup_dir)`、`plan_sync(client_root, server_root, manifest)`、`apply_sync(plan, client_root, server_root, backup_dir, on_progress=None)`、`build_manifest_from_targets(targets, game_root)`、`is_server_side(fmt)` — 跨 Task 2–9 命名一致。
- `TranslationTarget` 建構參數（`source_file`/`path_in_jar`/`mod_id`/`format`/`output_mode`/`target_file`）比對 `scanner.py` dataclass 欄位一致。
- worker 掛鉤用的 `game_root`、`self._targets`、`process_target` 回傳 `(n_t, n_c, n_f, failed)` 皆比對現有 worker.py。
