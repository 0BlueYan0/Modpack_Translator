# 遠端 API 速度/ETA 修正與 GUI 運行 Log 區 — 設計文件

日期:2026-07-02
狀態:已核准

## 背景與問題

### 問題 1:遠端 API 模式下速度與預計剩餘時間幾乎永遠不顯示

`main_window.py` 的統計標籤(`_update_stats_label`)為本地 llama.cpp 模型的速度調校:

1. **8 秒停滯判定**(`_STALL_SECS = 8.0`):超過 8 秒沒有任何字串完成,標籤改顯示
   「翻譯中…/計算中…」。遠端 API 每條字串是一次序列網路請求(連線+排隊+生成),
   單條超過 8 秒很常見,標籤幾乎永遠卡在停滯狀態。
2. **30 秒滑動視窗**(`_SPEED_WINDOW = 30.0`):需要視窗內至少 2 筆完成樣本才能算速度。
   遠端一條字串要 10~30 秒時,視窗內常湊不滿 2 筆,只顯示「—」。

兩個後端的管線相同(逐條序列請求、`on_pair_done` 逐條回報),差別只在單條延遲,
因此修正只需要調整 GUI 端的統計計算,不動管線。

### 問題 2:翻譯過程中 GUI 沒有任何運行 log

`TranslateWorker` 已有 `log` Signal 且發出大量有用訊息(「正在連線遠端 API…」、
「已備份 N 個 jar」、「[警告] 略過 xxx」、「進度已儲存…」),但
`_start_translation()` 從未連接 `log` signal,訊息全部被丟棄。`ScanWorker.log`
(「偵測到遊戲根目錄」)同樣未連接。

GUI 下方已有「掃描結果」文字區(`log_edit`),翻譯完成摘要已寫在該區。

## 設計決定(使用者已確認)

- Log 呈現:**沿用現有文字區**,改名「輸出記錄」,不改版面結構。
- Log 詳細度:**每個檔案一行**,加上既有的連線/備份/警告/儲存訊息。
- 不新增任何設定選項。

## 方案

### 1. 速度/ETA 計算(`main_window.py`)

- 維持 30 秒滑動視窗:視窗內 ≥2 筆樣本時照舊計算 — 本地模式行為完全不變。
- **退回機制**:視窗不足 2 筆樣本、但 `pairs_done ≥ 1` 且 `elapsed > 0` 時,
  改用累計平均速度 `pairs_done / elapsed` 計算速度與 ETA,速度後標示「(平均)」:

  ```
  速度:0.08 句/秒(平均)  |  已用時間:00:12:34  |  預計剩餘:03:20:11
  ```

- 「翻譯中…/計算中…」只在一條字串都尚未完成時顯示;之後永遠顯示數字,
  8 秒停滯判定不再蓋掉數字。
- 速度/ETA 計算抽成純函式(輸入:樣本、現在時刻、開始時刻、已完成對數、總對數;
  輸出:速度字串與 ETA 字串),不觸碰 widget,可單元測試遠端慢速情境。

### 2. Log 區接線(`main_window.py` + `worker.py`)

- `_start_translation()` 補上 `translate_worker.log.connect(self._append_log)`;
  `_on_scan()` 接上 `scan_worker.log`。
- 新增 `MainWindow._append_log(msg)`:附加一行 `[HH:MM:SS] 訊息` 到 `log_edit`,
  自動捲到底。
- 每個檔案開始時 log 區顯示一行:`(13/87) 翻譯 mod_id(JSON 語言檔)…`。
  作法:`TranslateWorker.progress` signal 增加 `format` 參數
  (`Signal(int, int, str, int)` → `Signal(int, int, str, str, int)`),
  由 GUI 端 `_on_translate_progress` 用既有 `_FMT_NAME_MAP` 組出該行附加到 log 區。
  `_FMT_NAME_MAP` 留在 `main_window.py`,不搬移、不重複定義。
- 區塊標題「掃描結果」改為「輸出記錄」。
- 翻譯開始時不清空既有內容,先附加一條分隔線再逐行附加(與現有完成摘要行為一致)。
- 既有 `log.emit` 訊息(連線中、已備份、警告略過、進度已儲存、失敗項目)
  自然全部顯示,不需改動。

## 錯誤處理

- 統計退回機制純屬顯示層,無新錯誤路徑;`elapsed == 0` 時維持顯示「—」。
- Log 附加為 GUI 執行緒 slot(Qt queued connection 自動跨執行緒),無競態。

## 測試

- 新增純函式單元測試:
  - 視窗內 ≥2 筆樣本 → 沿用視窗速度(既有行為)。
  - 視窗 <2 筆但已完成 ≥1 條 → 回傳平均速度並帶「(平均)」標示。
  - 尚未完成任何字串 → 顯示「翻譯中…/計算中…」。
  - ETA 以剩餘對數 / 速度計算,`total_pairs` 以 `max(scan_total, done+1)` 夾住。
- Log 接線以手動驗證(GUI):掃描與翻譯時逐行出現訊息、自動捲動、完成摘要照常。

## 不做的事(YAGNI)

- 不做 token 級進度回報(改動管線,收益低)。
- 不做 log 等級/過濾選項。
- 不做獨立 log 面板或分頁。
