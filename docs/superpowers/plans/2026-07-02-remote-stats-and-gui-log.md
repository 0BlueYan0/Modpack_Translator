# 遠端 API 速度/ETA 修正與 GUI 運行 Log 區 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 遠端 API 慢速時統計標籤退回累計平均速度顯示速度/ETA,並把 worker 既有的 log signal 接到 GUI 現有文字區逐行顯示運行狀況。

**Architecture:** 統計計算抽成無 Qt 依賴的純函式 `build_stats_text()`(新模組 `gui/stats.py`),`MainWindow._update_stats_label` 改為薄轉接;log 部分僅是把既有 `ScanWorker.log`/`TranslateWorker.log` signal 接上現有 `log_edit`,並在 `progress` signal 加上 `format` 參數讓 GUI 端組出每檔案一行的進度 log。不動翻譯管線。

**Tech Stack:** Python 3.11+、PySide6(GUI)、pytest(測試)。專案採 src layout,venv 於 `.venv/`。

**Spec:** `docs/superpowers/specs/2026-07-02-remote-stats-and-gui-log-design.md`

## Global Constraints

- 所有使用者可見文字為繁體中文,標點與既有 UI 一致(全形冒號「：」、全形括號「（）」)。
- 統計標籤格式:`速度：{...}  |  已用時間：HH:MM:SS  |  預計剩餘：{...}`(分隔為 2 空格+`|`+2 空格)。
- 本地模式行為不變:滑動視窗 30 秒內 ≥2 筆樣本時照舊用視窗速度。
- 不新增任何設定選項、不改版面結構、不動翻譯管線(runner/translator/_chat)。
- 測試指令:`.venv/Scripts/python.exe -m pytest tests -q`(目前 32 passed,不得變少)。
- 每個 task 結束都要 commit,訊息格式沿用 repo 慣例(`feat:`/`fix:` + 繁中描述)。

---

### Task 1: 統計純函式 `build_stats_text()` + 單元測試

**Files:**
- Create: `src/modpack_translator/gui/stats.py`
- Test: `tests/test_gui_stats.py`

**Interfaces:**
- Produces: `build_stats_text(now: float, start_time: float, samples: Sequence[tuple[float, int]], pairs_done: int, total_pairs: int) -> str`,以及常數 `SPEED_WINDOW: float = 30.0`。Task 2 的 `MainWindow._update_stats_label` 會呼叫它。`samples` 是 `(time.monotonic() 時間戳, 累計完成對數)` 序列(GUI 端實際傳 `deque`)。

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_gui_stats.py`:

```python
from modpack_translator.gui.stats import build_stats_text


def test_window_speed_when_samples_dense():
    # 視窗內 2 筆樣本、10 秒完成 20 對 → 視窗速度 2.0 句/秒(本地模式既有行為)
    samples = [(100.0, 0), (110.0, 20)]
    text = build_stats_text(
        now=110.0, start_time=100.0, samples=samples,
        pairs_done=20, total_pairs=220,
    )
    # 剩餘 200 對 / 2.0 = 100 秒
    assert text == "速度：2.0 句/秒  |  已用時間：00:00:10  |  預計剩餘：00:01:40"


def test_average_fallback_when_window_sparse():
    # 遠端慢速:視窗內只有 1 筆樣本 → 退回累計平均 4/128 = 0.03125 句/秒
    # (數值刻意選二進位可精確表示,避免浮點誤差影響斷言)
    samples = [(120.0, 4)]
    text = build_stats_text(
        now=128.0, start_time=0.0, samples=samples,
        pairs_done=4, total_pairs=100,
    )
    # 剩餘 96 對 / 0.03125 = 3072 秒 = 00:51:12
    assert text == "速度：0.03 句/秒（平均）  |  已用時間：00:02:08  |  預計剩餘：00:51:12"


def test_stalled_window_falls_back_to_average():
    # 視窗內 2 筆樣本但完成數沒有前進(單條長推理)→ 不顯示停滯字樣,退回平均
    samples = [(150.0, 5), (155.0, 5)]
    text = build_stats_text(
        now=160.0, start_time=0.0, samples=samples,
        pairs_done=5, total_pairs=10,
    )
    # 平均 5/160 = 0.03125 句/秒;剩餘 5 對 → 160 秒
    assert text == "速度：0.03 句/秒（平均）  |  已用時間：00:02:40  |  預計剩餘：00:02:40"


def test_before_first_pair_shows_translating():
    # 一條都還沒完成(連線中或第一條推理中)→ 顯示翻譯中/計算中
    text = build_stats_text(
        now=20.0, start_time=0.0, samples=[],
        pairs_done=0, total_pairs=50,
    )
    assert text == "速度：翻譯中…  |  已用時間：00:00:20  |  預計剩餘：計算中…"


def test_total_pairs_clamped_to_done_plus_one():
    # 掃描估算偏低時 total 以 done+1 夾住,剩餘至少 1 對
    samples = [(0.0, 0), (10.0, 10)]
    text = build_stats_text(
        now=10.0, start_time=0.0, samples=samples,
        pairs_done=10, total_pairs=8,
    )
    assert text == "速度：1.0 句/秒  |  已用時間：00:00:10  |  預計剩餘：00:00:01"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_stats.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'modpack_translator.gui.stats'`

- [ ] **Step 3: 寫最小實作**

建立 `src/modpack_translator/gui/stats.py`(注意:此模組**不得** import 任何 Qt,保持可獨立測試):

```python
from __future__ import annotations

from typing import Sequence

SPEED_WINDOW = 30.0  # 秒,滑動視窗寬度


def _format_hms(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _window_speed(now: float, samples: Sequence[tuple[float, int]]) -> float | None:
    """最近 SPEED_WINDOW 秒內有 ≥2 筆樣本且有進度時回傳視窗速度,否則 None。"""
    cutoff = now - SPEED_WINDOW
    window = [(t, p) for t, p in samples if t >= cutoff]
    if len(window) < 2:
        return None
    dt = window[-1][0] - window[0][0]
    dp = window[-1][1] - window[0][1]
    if dt <= 0 or dp <= 0:
        return None
    return dp / dt


def build_stats_text(
    now: float,
    start_time: float,
    samples: Sequence[tuple[float, int]],
    pairs_done: int,
    total_pairs: int,
) -> str:
    """組出統計標籤文字。samples 為 (monotonic 時間戳, 累計完成對數)。

    速度優先用滑動視窗計算(本地模型的即時速度);視窗樣本不足或無進度、
    但已有完成對數時,退回「開始至今的累計平均」並標示(平均),
    確保遠端慢速 API(單條 >8 秒)也永遠有數字與 ETA 可顯示。
    """
    elapsed = max(0.0, now - start_time)
    elapsed_str = _format_hms(int(elapsed))

    speed = _window_speed(now, samples)
    is_average = False
    if speed is None and pairs_done >= 1 and elapsed > 0:
        speed = pairs_done / elapsed
        is_average = True

    if speed is None or speed <= 0:
        # 尚未有任何字串完成:連線中或第一條仍在推理
        speed_part = "翻譯中…"
        eta_str = "計算中…"
    else:
        speed_str = f"{speed:.2f}" if speed < 1 else f"{speed:.1f}"
        suffix = "（平均）" if is_average else ""
        speed_part = f"{speed_str} 句/秒{suffix}"
        total = max(total_pairs, pairs_done + 1)
        remaining = max(0, total - pairs_done)
        eta_str = _format_hms(int(remaining / speed))

    return f"速度：{speed_part}  |  已用時間：{elapsed_str}  |  預計剩餘：{eta_str}"
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gui_stats.py -v`
Expected: 5 passed

- [ ] **Step 5: 跑完整測試套件**

Run: `.venv/Scripts/python.exe -m pytest tests -q`
Expected: 37 passed(原 32 + 新 5)

- [ ] **Step 6: Commit**

```bash
git add src/modpack_translator/gui/stats.py tests/test_gui_stats.py
git commit -m "feat: 新增統計純函式 build_stats_text（視窗速度＋累計平均退回）"
```

---

### Task 2: MainWindow 改用 `build_stats_text`,移除 8 秒停滯判定

**Files:**
- Modify: `src/modpack_translator/gui/main_window.py`(`_update_stats_label` 約 651-697 行、`__init__` 約 90-92 行、`_start_translation` 約 827-836 行、`_on_pair_progress` 約 854-861 行)

**Interfaces:**
- Consumes: Task 1 的 `build_stats_text(now, start_time, samples, pairs_done, total_pairs) -> str`。
- Produces: 無(內部 GUI 行為)。`self._speed_samples`(deque)、`self._pairs_done`、`self._translation_start_time`、`self._scan_total_pairs` 維持原名,Task 3 不依賴本 task。

- [ ] **Step 1: 加 import**

在 `main_window.py` 的 `from modpack_translator.gui.worker import ScanWorker, TranslateWorker` 之後加入:

```python
from modpack_translator.gui.stats import build_stats_text
```

- [ ] **Step 2: 替換 `_update_stats_label` 與刪除停滯狀態**

(a) 刪除類別常數(位於 `_set_busy` 之後):

```python
    _SPEED_WINDOW = 30.0   # 秒，滑動視窗寬度
    _STALL_SECS   = 8.0    # 超過此秒數無進度 → 顯示「翻譯中…」
```

(b) 整個 `_update_stats_label` 方法(原約 45 行)替換為:

```python
    def _update_stats_label(self):
        self.stats_label.setText(build_stats_text(
            now=time.monotonic(),
            start_time=self._translation_start_time,
            samples=self._speed_samples,
            pairs_done=self._pairs_done,
            total_pairs=self._scan_total_pairs,
        ))
```

(c) `__init__` 中刪除這一行(約 92 行):

```python
        self._last_pair_time: float = 0.0
```

(d) `_on_pair_progress` 中刪除這一行:

```python
        self._last_pair_time = now
```

(e) `_start_translation` 中刪除:

```python
        self._last_pair_time = time.monotonic()
```

並把初始標籤文字這一行:

```python
        self.stats_label.setText("速度：— 句/秒  |  已用時間：00:00:00  |  預計剩餘：—")
```

替換為(統一由純函式組字,開始當下顯示「翻譯中…/計算中…」):

```python
        self._update_stats_label()
```

注意:此行必須保持在 `self._translation_start_time = time.monotonic()`、`self._pairs_done = 0`、`self._speed_samples.clear()` 之後才呼叫。

- [ ] **Step 3: 確認殘留引用已清除**

Run: `grep -n "_last_pair_time\|_STALL_SECS\|_SPEED_WINDOW" src/modpack_translator/gui/main_window.py`
Expected: 無任何輸出(exit code 1)

- [ ] **Step 4: 跑完整測試 + GUI 冒煙啟動**

Run: `.venv/Scripts/python.exe -m pytest tests -q`
Expected: 37 passed

Run(確認 import 與視窗建構無誤,3 秒後自動關閉):

```bash
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
app = QApplication([])
from modpack_translator.gui.main_window import MainWindow
w = MainWindow(); w.show()
QTimer.singleShot(3000, app.quit)
app.exec()
print('GUI OK')
"
```

Expected: 印出 `GUI OK`,無 traceback

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/gui/main_window.py
git commit -m "fix: 遠端 API 慢速時速度／預計剩餘退回累計平均，不再卡在翻譯中"
```

---

### Task 3: Log 區接線 — worker log signal 連接、每檔案一行進度、標題改名

**Files:**
- Modify: `src/modpack_translator/gui/worker.py`(`TranslateWorker.progress` signal 定義約 120 行、`run()` 內 emit 約 197 行)
- Modify: `src/modpack_translator/gui/main_window.py`(`_build_ui` 掃描結果標題約 412 行、`_on_scan` 約 714-722 行、`_on_scan_finished` 約 745 與 773 行、`_start_translation` 約 838-848 行、`_on_translate_progress` 約 850-852 行,新增 `_append_log`)

**Interfaces:**
- Consumes: `MainWindow._FMT_NAME_MAP: dict[str, str]`(既有,格式代碼→中文名)、`ScanWorker.log: Signal(str)`、`TranslateWorker.log: Signal(str)`(既有,未接)。
- Produces: `TranslateWorker.progress` 簽名改為 `Signal(int, int, str, str, int)` = (current_idx, total, mod_id, format, pairs_done_so_far);`MainWindow._append_log(msg: str) -> None`。

- [ ] **Step 1: worker.py — progress signal 加 format 參數**

`TranslateWorker` 類別中,把:

```python
    progress     = Signal(int, int, str, int) # current_idx, total, mod_id, pairs_done_so_far
```

改為:

```python
    progress     = Signal(int, int, str, str, int) # current_idx, total, mod_id, format, pairs_done_so_far
```

`run()` 迴圈中,把:

```python
                    self.progress.emit(i, total, target.mod_id, total_pairs_done)
```

改為:

```python
                    self.progress.emit(i, total, target.mod_id, target.format, total_pairs_done)
```

- [ ] **Step 2: main_window.py — 新增 `_append_log` 並更新 `_on_translate_progress`**

在 `_copy_log` 方法後新增:

```python
    def _append_log(self, msg: str):
        """附加一行帶時間戳的訊息到輸出記錄區並捲到底。worker signal 經 queued connection 呼叫,固定在 GUI 執行緒執行。"""
        ts = time.strftime("%H:%M:%S")
        self.log_edit.append(f"[{ts}] {msg}")
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)
```

把 `_on_translate_progress` 整個替換為:

```python
    def _on_translate_progress(self, current: int, total: int, mod_id: str, fmt: str, pairs_done: int):
        # 追蹤目前第幾個檔案並在 log 區顯示;進度條由 _on_pair_progress 逐條更新
        self._current_progress = current + 1
        display_fmt = _FMT_NAME_MAP.get(fmt, fmt)
        self._append_log(f"({current + 1}/{total}) 翻譯 {mod_id}（{display_fmt}）…")
```

- [ ] **Step 3: main_window.py — 接上兩個 worker 的 log signal 與翻譯開始分隔線**

`_on_scan` 中,在 `self._scan_worker.finished.connect(self._on_scan_finished)` 之前加入:

```python
        self._scan_worker.log.connect(self._append_log)
```

`_start_translation` 中,在 `self._translate_worker.progress.connect(self._on_translate_progress)` 之前加入:

```python
        self._translate_worker.log.connect(self._append_log)
```

同樣在 `_start_translation`,建立 worker(`self._translate_worker = TranslateWorker(`)之前加入分隔線(不帶時間戳,與完成摘要的分隔線樣式一致):

```python
        self.log_edit.append("\n" + "─" * 40)
```

- [ ] **Step 4: main_window.py — 掃描結果改為附加而非覆寫,標題改名**

`_build_ui` 中,把:

```python
        result_lbl = QLabel("掃描結果")
```

改為:

```python
        result_lbl = QLabel("輸出記錄")
```

`_on_scan_finished` 中,把無目標分支的:

```python
            self.log_edit.setPlainText("掃描完成 — 未找到可翻譯的檔案。")
```

改為:

```python
            self.log_edit.append("掃描完成 — 未找到可翻譯的檔案。")
```

以及結尾的:

```python
        self.log_edit.setPlainText("\n".join(lines))
```

改為(保留掃描期間的 log 行,例如「偵測到遊戲根目錄」;`_on_scan` 開頭的 `setPlainText("")` 清空維持不變):

```python
        self.log_edit.append("\n".join(lines))
```

- [ ] **Step 5: 跑完整測試 + GUI 冒煙啟動**

Run: `.venv/Scripts/python.exe -m pytest tests -q`
Expected: 37 passed

Run(同 Task 2 Step 4 的 GUI 冒煙指令):

```bash
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
app = QApplication([])
from modpack_translator.gui.main_window import MainWindow
w = MainWindow(); w.show()
QTimer.singleShot(3000, app.quit)
app.exec()
print('GUI OK')
"
```

Expected: 印出 `GUI OK`,無 traceback

- [ ] **Step 6: Commit**

```bash
git add src/modpack_translator/gui/worker.py src/modpack_translator/gui/main_window.py
git commit -m "feat: GUI 輸出記錄區即時顯示掃描與翻譯運行 log"
```

---

### 手動驗收(全部 task 完成後,由使用者執行)

1. `.venv/Scripts/python.exe main.py` 啟動 GUI。
2. 掃描測試模組包 → 「輸出記錄」區先出現 `[HH:MM:SS] 偵測到遊戲根目錄：…`,接著附加掃描結果摘要。
3. 切到「遠端 API」後端,開始翻譯 → log 區依序出現分隔線、`正在連線遠端 API，請稍候…`、`模型服務已就緒，開始翻譯…`、每個檔案一行 `(n/總數) 翻譯 mod_id（格式）…`,自動捲到底。
4. 統計標籤:第一條完成前顯示「速度：翻譯中… | … | 預計剩餘：計算中…」;之後即使單條超過 8 秒,速度/ETA 仍持續顯示數字(慢速時帶「（平均）」)。
5. 本地模式跑一段 → 速度顯示不帶「（平均）」(視窗速度,行為同舊版)。
