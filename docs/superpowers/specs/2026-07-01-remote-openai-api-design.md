# 遠端 OpenAI 相容 API 支援 — 設計規格

- 日期：2026-07-01
- 狀態：設計已核准，待實作
- 範圍：讓翻譯器除了現有的本地 llama.cpp 模型外，也能使用遠端 OpenAI 相容 API（OpenAI、OpenRouter、Groq、自架 vLLM 等），本地 / 遠端可於 GUI 切換。

## 1. 背景與現況

翻譯核心 `GGUFTranslator`（`src/modpack_translator/pipeline/translator.py`）**本來就是**用官方 `openai` Python SDK 對 OpenAI 相容的 `/v1/chat/completions` 端點做串流翻譯：

```python
from openai import OpenAI
self._client = OpenAI(base_url=f"{self._base_url}/v1", api_key=api_key)
stream = self._client.chat.completions.create(..., stream=True)
```

本地 llama.cpp server 只是「其中一個」OpenAI 相容後端。目前的限制：

1. `GGUFTranslator` 綁定本地 server 生命週期（`subprocess` 啟動 llama-server、Windows Job 物件、健康輪詢、log 追蹤），約 420 行，遠端模式下大半是死碼。
2. `translate()` 無條件送出 `extra_body={"repeat_penalty": ...}`（`translator.py:417`）。`repeat_penalty` 是 llama.cpp 專屬取樣參數，**正牌 `api.openai.com` 會回 400「Unrecognized request argument」**。
3. 端點設定的讀取優先序為「環境變數 → `.runtime/backend.json` → `configs/model.yaml`」（`translator.py:268-287`）。`backend.json` 由 setup 寫入且含本地 `server_url`，會**蓋掉** `model.yaml` 的設定 → 光改 `model.yaml` 可能無效（優先序陷阱）。
4. GUI「模型設定」只有 LoRA / 基礎模型 / GPU 層數，無遠端欄位；`_start_translation()` 會硬性檢查 LoRA 檔存在（`main_window.py:629-635`）。
5. `worker.py` 的 per-target `except Exception` 會「記錄後 `continue`」（`worker.py:195-211`），系統性錯誤（如金鑰錯誤）會被逐條吞掉，最後顯示「翻譯 0 組」卻無明確報錯。

## 2. 需求決策

| 決策 | 選擇 |
|---|---|
| 定位 | 本地 / 遠端**可切換**並存 |
| 設定介面 | GUI 面板，設定存 QSettings |
| 供應商設定 | 通用欄位（Base URL / API Key / 模型名），不綁特定廠商 |
| 測試連線 | 提供「測試連線」按鈕 |
| 金鑰儲存 | QSettings 明文（Windows 為註冊表，僅目前使用者帳號可讀） |

## 3. 架構

```
                       ┌─ backend_mode == "local"  → GGUFTranslator（現況，啟動本地 server）
build_translator(cfg) ─┤
                       └─ backend_mode == "remote" → RemoteTranslator（直連遠端 /v1）

兩者共用 stream_chat(client, model, system_prompt, text, max_tokens, temperature, extra_body, cancel_check)
```

取向：**拆分 + 工廠函式**。把兩者共用的「OpenAI 串流翻譯迴圈」抽成小 helper，保留 `GGUFTranslator`（本地），新增精簡的 `RemoteTranslator`，用工廠 `build_translator` 依模式回傳。兩者對外介面一致（`translate(text, cancel_check=None)` / `close()` / context manager），呼叫端不需分辨。

### 3.1 檔案配置（避免循環 import）

- `pipeline/_chat.py`（新）— `stream_chat(...)` 共用串流 + 取消迴圈。
- `pipeline/translator.py` — 保留 `GGUFTranslator`（本地，改呼叫 `stream_chat`），新增 `build_translator(cfg, system_prompt)` 工廠（內部延遲 import 遠端類別以避免相依 weight / 循環）。定義 `TranslatorFatalError` 例外（放此模組或 `_chat.py`，供兩端共用）。
- `pipeline/remote_translator.py`（新）— `RemoteTranslator` + `test_remote_connection`。

### 3.2 `ModelConfig` 新增欄位

於 `src/modpack_translator/config.py` 的 `ModelConfig` 新增，並同步 `configs/model.yaml` 註解：

```python
backend_mode: str = "local"      # "local" | "remote"
remote_base_url: str = ""
remote_api_key: str = ""
remote_model: str = ""
```

刻意使用**全新 `remote_*` 欄位**，不重用既有 `server_url/server_api_key/server_model`（那組會被 `.runtime/backend.json` 蓋掉，即第 1 節的優先序陷阱）。

**遠端欄位讀取優先序**：明確設定來源（GUI QSettings，經由 `_build_cfg` 注入 `cfg.model`；或 `configs/model.yaml`）優先採用。環境變數 `MODPACK_TRANSLATOR_REMOTE_URL` / `_API_KEY` / `_MODEL` 僅在對應欄位留空時作為備援填補，不會覆蓋已設定的值。CLI 使用者可直接於 yaml 設定，或留空由環境變數補上。

### 3.3 關鍵行為差異

- `repeat_penalty` **只在本地**放進 `extra_body`；遠端 `extra_body=None`，只送標準 OpenAI 參數（`model / messages / max_tokens / temperature / stream`）。
- 遠端 `close()` 為 no-op（無 server 要關）。
- `translate()` 的串流與**取消**行為兩端共用 `stream_chat`，一致。

## 4. GUI 面板（`src/modpack_translator/gui/main_window.py`）

在「模型設定」群組頂端加後端模式切換，下方兩組欄位依模式 `setVisible`：

```
┌─ 模型設定 ──────────────────────────────────────────┐
│ 後端模式:   (•) 本地模型     ( ) 遠端 API        [?] │
│ ───────────────────────────────────────────────── │
│ ▼ 本地模型時顯示（現況三欄，原樣保留）              │
│   LoRA 適配器 / 基礎模型 / GPU 層數                  │
│ ───────────────────────────────────────────────── │
│ ▼ 遠端 API 時顯示                                   │
│   Base URL:  [https://api.openai.com/v1 ....... ][?] │
│   API Key:   [••••••••••••••••••]  [👁 顯示]      [?] │
│   模型名稱:  [例如 gpt-4o-mini ................ ][?] │
│   [ 測試連線 ]   狀態：✓ 連線成功                    │
└────────────────────────────────────────────────────┘
```

- **模式切換**：兩個 `QRadioButton`（放進 `QButtonGroup`），切換時 `setVisible` 對應區塊；預設依 QSettings，沒有則 `local`。兩組欄位各自包在容器 widget 內以整塊切換。
- **API Key 欄**：`QLineEdit` 設 `EchoMode.Password`，旁邊「👁 顯示」按鈕切明碼。
- **Base URL / 模型名**：以 placeholder 提示範例，不預填，避免使用者誤用。
- **QSettings 鍵**：`model/backend_mode`、`model/remote_base_url`、`model/remote_api_key`、`model/remote_model`。啟動時載入填入欄位；欄位變動 / 切換模式時即時存回（比照現有 `ui/theme`）。
- **`_build_cfg()`**：讀目前模式 → 遠端則把 `backend_mode="remote"` 與 `remote_*` 塞進 `cfg.model`；本地維持現況（仍設 local 欄位，無害）。
- **`_start_translation()`**：遠端模式**跳過** LoRA 檔案存在檢查（`main_window.py:629-635`）。

## 5. 測試連線

核心邏輯做成可單元測試的純函式，GUI 於背景執行緒呼叫。

```python
# pipeline/remote_translator.py
def test_remote_connection(base_url, api_key, model, timeout=15) -> tuple[bool, str]:
    client = OpenAI(base_url=<normalized>, api_key=api_key, timeout=timeout)
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=1, stream=False,
    )
    return True, "連線成功"
```

用 1-token 的 chat completion 當探針：走的正是翻譯實際路徑（chat completions + 模型名），一次驗證 URL、金鑰、模型名。代價約 1 token（GUI 註明）。

**錯誤對應**（攔截 `openai` 例外轉中文）：

| 情況 | 回傳 |
|---|---|
| 成功 | `(True, "連線成功")` → ✓（綠） |
| `AuthenticationError`（401） | `(False, "API 金鑰錯誤或未授權")` |
| 找不到模型（404 / `NotFoundError`） | `(False, "找不到模型「{model}」，請確認模型名稱")` |
| `APIConnectionError` / 逾時 | `(False, "無法連線到 {base_url}，請確認網址")` |
| 其他 `APIStatusError` | `(False, "<狀態碼> <訊息>")` |

**GUI 端**：比照 `UpdateCheckWorker` 做 `ConnTestWorker`（QThread 子類別）避免卡 UI。測試中按鈕變灰、標籤「測試中…」；完成後回填帶顏色狀態標籤；保留 worker 參考避免被回收。

## 6. 呼叫端接線與錯誤處理

- **工廠接線**：`worker.py:169` 與 `translate_modpack.py:169` 把 `GGUFTranslator(...)` 改為 `build_translator(...)`。worker 連線提示依模式顯示（遠端 →「正在連線遠端 API…」）。
- **致命錯誤大聲中止**：遠端遇 `AuthenticationError` / `APIConnectionError` 等**系統性**錯誤時，`stream_chat` / `RemoteTranslator` 拋出 `TranslatorFatalError`。修改 `worker.py` per-target handler，在一般 `except Exception` 前先 `except TranslatorFatalError: raise`，讓它冒到 `self.error.emit`，直接彈錯中止整趟；單一字串的普通失敗仍照舊回退。CLI（`translate_modpack.py`）同理讓致命錯誤中止而非 per-target 吞掉。

## 7. 測試策略

專案目前無 `tests/`。新增最小 pytest 套件，全程 mock，不打網路：

1. `build_translator` 依 `backend_mode` 回傳正確類別（`GGUFTranslator` / `RemoteTranslator`）。
2. 遠端 `translate()` 的 `create` 呼叫**不含** `repeat_penalty`；本地路徑**含**（驗 `stream_chat` 的 `extra_body` 傳遞）。
3. `stream_chat` 的 `cancel_check` 中途回 True → 回傳空字串。
4. `test_remote_connection` 把 auth / 連線 / 找不到模型三種例外對應到正確 `(False, 訊息)`。
5. `ModelConfig` 接受新欄位與預設值。

- 將 `pytest` 加入 dev 依賴（`pyproject.toml`）。
- **實機驗證**：對真實遠端端點的端對端測試需使用者提供 API 金鑰；無金鑰時僅能 mock。此步驟需使用者協助或提供測試 key。

## 8. 影響檔案清單

- `src/modpack_translator/config.py` — 新增 `ModelConfig` 欄位。
- `configs/model.yaml` — 新增遠端欄位與註解。
- `src/modpack_translator/pipeline/_chat.py` — 新增，`stream_chat`。
- `src/modpack_translator/pipeline/translator.py` — 抽出串流迴圈、新增 `build_translator`、`TranslatorFatalError`。
- `src/modpack_translator/pipeline/remote_translator.py` — 新增，`RemoteTranslator` + `test_remote_connection`。
- `src/modpack_translator/gui/main_window.py` — 模式切換 UI、遠端欄位、測試連線、`_build_cfg` / `_start_translation` 調整、`ConnTestWorker`。
- `src/modpack_translator/gui/worker.py` — 改用工廠、致命錯誤處理、連線提示訊息。
- `scripts/translate_modpack.py` — 改用工廠、致命錯誤處理。
- `tests/`（新）— 單元測試。
- `pyproject.toml` — dev 依賴加 `pytest`。
- 文件（`README.md` / `README_zh.md`）— 補充遠端 API 使用說明（實作後）。

## 9. 非目標（YAGNI）

- 不做供應商下拉預設清單（用通用欄位）。
- 不移除本地後端。
- 不做遠端並發 / 批次請求優化（現為逐條串流）。
- 遠端不支援 `repeat_penalty` 等 llama.cpp 專屬參數（可日後再議）。
- 金鑰不做加密儲存（QSettings 明文，屬桌面 App 常規）。
