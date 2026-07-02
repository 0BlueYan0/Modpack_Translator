# Minecraft模組包翻譯器 v1.5.1

**Language / 語言：** [English](README.md) | 繁體中文

[![Ko-fi](https://img.shields.io/badge/贊助我-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/koudesuk)

---

自動將 Minecraft 模組包語言檔從英文（`en_us`）翻譯為繁體中文（`zh_tw`）的工具，底層使用 GGUF 格式的微調模型搭配 LoRA 適配器。提供圖形化介面（GUI）與命令列介面（CLI）。

---

## 系統需求

| 需求 | 版本 | 說明 |
|---|---|---|
| [Git](https://git-scm.com/downloads) | 任意版本 | clone 倉庫所需 |
| [Git LFS](https://git-lfs.com) | 任意版本 | **必須安裝** — LoRA 適配器（約 66 MB）透過 LFS 儲存 |
| [uv](https://docs.astral.sh/uv/) | 最新版 | 安裝並管理本專案使用的 Python runtime |
| GPU（可選） | NVIDIA CUDA 或支援的 AMD ROCm | 強烈建議；純 CPU 可用但速度非常慢 |
| [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) | 12.4 或更新版本 | **NVIDIA CUDA 後端必須安裝**；只有 Game Ready/Studio Driver 不夠。cuDNN 不需要 |
| 可用磁碟空間 | 約 6 GB | 適配器 ~66 MB（LFS）＋基礎模型 ~5 GB（自動下載） |

---

## 安裝步驟

### 第一步 — 安裝 uv

`uv` 是本專案使用的 Python 套件管理器，在您的電腦上安裝一次即可：

**Windows（PowerShell）：**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux：**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 第二步 — 安裝 Git LFS

LoRA 適配器透過 Git LFS 儲存，**clone 前必須先安裝 Git LFS**：

**Windows：** 從 [git-lfs.com](https://git-lfs.com) 下載安裝程式，或執行：
```powershell
winget install GitHub.GitLFS
```

**macOS：**
```bash
brew install git-lfs
```

**Linux（Ubuntu/Debian）：**
```bash
sudo apt install git-lfs
```

安裝完成後，為您的帳號啟用一次：
```bash
git lfs install
```

### 第三步 — Clone 倉庫

```bash
git clone <repository-url>
cd Modpack_Translator
```

Git LFS 會在 clone 時自動下載適配器。請確認檔案大小約為 **66 MB**（若只有幾百位元組，代表只下載到指標檔）：

```bash
# macOS/Linux
ls -lh adapter/minecraft_translator_gemma4_e4b_lora.gguf

# Windows
dir adapter\minecraft_translator_gemma4_e4b_lora.gguf

# 若檔案太小（指標檔），請執行：
git lfs pull
```

### NVIDIA GPU 使用者 — 安裝 CUDA Toolkit

如果要使用 CUDA 後端，請在執行初始化前先安裝 **CUDA Toolkit 12.4 或更新版本**：

```text
https://developer.nvidia.com/cuda-downloads
```

NVIDIA Game Ready/Studio Driver 只提供驅動程式函式庫；本專案使用的 CUDA `llama-cpp-python` wheel 還需要 CUDA runtime/cuBLAS 函式庫，例如 Windows 上的 `cudart64_12.dll` 與 `cublas64_12.dll`。初始化腳本會檢查這些函式庫，缺少時會印出明確錯誤訊息。

cuDNN **不需要**安裝。

### 第四步 — 執行後端初始化

初始化腳本會安裝 uv 管理的 CPython 3.12、建立 `.venv/`、偵測硬體、安裝對應的本機推理後端、下載基礎模型，並寫入 `.runtime/backend.json`。使用者不需要另外安裝 Python。

**Windows：**
```bat
setup_windows.bat
```

初始化完成後，Windows 會在專案資料夾建立版本化 launcher，例如 `模組包翻譯器v1.5.1.exe`。之後直接雙擊它即可啟動程式，不需要開終端機手動輸入命令。若 launcher 遺失，請重新執行 setup，或手動建立：

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows_launcher.ps1
```

**macOS / Linux：**
```bash
./setup_unix.sh
```

硬體會自動選擇：

| 硬體 | 後端 |
|---|---|
| NVIDIA | CUDA `llama-cpp-python[server]` wheel |
| AMD Windows/Linux | AMD 預先編譯的 `llama.cpp` / `llama-server` binary |
| 僅 CPU | CPU `llama-cpp-python[server]` wheel |

重新執行初始化前請先關閉翻譯器。Windows 上正在執行的本機模型服務會鎖住 `.dll` 檔案，導致後端替換失敗。

---

## 後端初始化覆寫

一般使用者用自動偵測即可。若要強制指定後端：

**Windows：**
```bat
setup_windows.bat --backend cuda
setup_windows.bat --backend amd
setup_windows.bat --backend cpu
```

**macOS / Linux：**
```bash
./setup_unix.sh --backend cuda
./setup_unix.sh --backend amd
./setup_unix.sh --backend cpu
```

程式會透過 OpenAI-compatible 本機 HTTP API 呼叫模型。若您自行啟動相容 server，也可以設定 `LLAMA_SERVER_URL`，例如 `http://127.0.0.1:8888/v1`。

### 使用遠端 OpenAI 相容 API（可選）

除了本機模型，也可改用遠端 OpenAI 相容端點（OpenAI、OpenRouter、Groq、自架 vLLM 等）。

**GUI：** 在「模型設定 → 後端模式」選「遠端 API」，填入 Base URL（例如 `https://api.openai.com/v1`）、
API Key 與模型名稱（例如 `gpt-4o-mini`），可按「測試連線」確認設定正確。設定會保存在本機。

**CLI／進階：** 於 `configs/model.yaml` 設定 `backend_mode: "remote"` 與 `remote_base_url` /
`remote_api_key` / `remote_model`；任一欄位留空時，會以環境變數 `MODPACK_TRANSLATOR_REMOTE_URL` /
`MODPACK_TRANSLATOR_REMOTE_API_KEY` / `MODPACK_TRANSLATOR_REMOTE_MODEL` 作為備援填補（設定檔中已填寫的值優先）。

注意：遠端模式按供應商計費（模組包字串眾多），但有翻譯快取，重跑僅計費新字串。

若修改了 `configs/model.yaml` 中的基礎模型、LoRA、context size、GPU 層數或後端類型，請重新執行初始化腳本，讓 `.runtime/backend.json` 重新產生。

---

## 設定檔說明

### `configs/model.yaml`

```yaml
model:
  base_gguf_path: ""                              # 留空自動下載
  base_hf_repo: "unsloth/gemma-4-E4B-it-GGUF"
  base_hf_filename: "gemma-4-E4B-it-Q4_K_M.gguf"
  lora_gguf_path: "adapter/minecraft_translator_gemma4_e4b_lora.gguf"
  lora_scale: 1.0
  n_gpu_layers: -1     # -1 = 全部卸載至 GPU，0 = 僅 CPU
  n_ctx: 2048
  max_tokens: 512
  temperature: 0.05
  repeat_penalty: 1.1
  verbose: false
  server_url: "http://127.0.0.1:8888/v1"
  server_api_key: "llama.cpp"
  server_model: "local-model"
  auto_start_server: true
  server_ready_timeout: 600
```

### `configs/paths.yaml`

```yaml
paths:
  output_root: "outputs"
  resource_pack_dir: "outputs/resource_packs"
  translation_cache: "outputs/translation_cache.json"
```

### `configs/languages/zh_tw.yaml`

包含語言代碼、顯示名稱及翻譯模型的系統提示詞。除非要新增其他目標語言，否則請勿修改此檔案。

---

## GUI 使用方法

啟動圖形化介面：

```bash
uv run python main.py
```

Windows 使用者也可以直接雙擊版本化 launcher，例如 `模組包翻譯器v1.5.1.exe`；它會先檢查是否已完成 setup，再在背景執行 `uv run python main.py`，launcher 錯誤會寫到 `.runtime/launcher.log`。

啟動時，程式會在背景檢查最新 GitHub Release。有新版 release package 時才顯示更新視窗；沒有更新時不顯示任何訊息。自動更新會下載 release ZIP，若有 SHA256 檔會先驗證，接著套用新版原始碼、移除舊 `.venv` 與過期的本機後端 runtime 檔案、重新執行 setup，完成後再啟動新版程式。

**操作步驟：**

1. **模組包資料夾** — 點擊「瀏覽…」選擇模組包實例目錄（包含 `mods/`、`config/` 的資料夾）。
2. **模型設定** — 一般安裝流程已由初始化腳本設定本機模型服務。只有在重新產生後端設定時才需要修改這些欄位。
3. **選項** — 勾選「翻譯模組 (.jar)」或「翻譯任務書」，並設定重試次數（預設 3）。
4. **掃描** — 點擊「🔍 掃描模組包」，輸出記錄面板顯示目標數量與樣本字串。
5. **翻譯** — 點擊「▶ 開始翻譯」，進度條顯示百分比、速度、已用時間及預計剩餘時間，輸出記錄面板即時顯示每個檔案的翻譯進度與警告訊息。
6. **完成** — 翻譯完成後，進度條變綠，按鈕顯示「✓ 完成」。

**原始檔案備份位置：**
- 模組 JAR → `mods_bak/`
- 任務設定 → `quests_bak/`

**失敗項目**（重試後仍無法翻譯的字串）會寫入 `Failed Items/<模組名稱>.txt`，供使用者檢查。若無失敗項目，此資料夾不會被建立。

---

## CLI 使用方法

```bash
uv run python scripts/translate_modpack.py --modpack <路徑> [選項]
```

### 參數說明

| 參數 | 預設值 | 說明 |
|---|---|---|
| `--modpack PATH` | （必填） | 模組包實例資料夾路徑 |
| `--language FILE` | `configs/languages/zh_tw.yaml` | 語言設定檔 |
| `--model-config FILE` | `configs/model.yaml` | 模型設定檔 |
| `--paths-config FILE` | `configs/paths.yaml` | 路徑設定檔 |
| `--dry-run` | false | 僅掃描，不執行翻譯 |
| `--skip-mods` | false | 略過模組 JAR 掃描 |
| `--skip-quests` | false | 略過任務設定掃描 |
| `--max-steps N` | -1（全部） | 限制翻譯前 N 個目標（測試用） |
| `--retry N` | 0 | 後處理驗證失敗時每個字串的重試次數 |

### 使用範例

```bash
# 預覽將要翻譯的內容（不實際翻譯）
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --dry-run

# 完整翻譯，失敗時最多重試 3 次
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --retry 3

# 僅翻譯任務書
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --skip-mods --retry 2
```

---

## 支援的檔案格式

| 格式代碼 | 副檔名 | 說明 |
|---|---|---|
| `json_lang` | `.json` | 標準模組語言檔（`assets/<mod>/lang/en_us.json`） |
| `legacy_lang` | `.lang` | 1.13 以前的舊式語言檔（`en_us.lang`） |
| `patchouli_json` | `.json` | Patchouli 導覽書頁面 |
| `ftbq_snbt` | `.snbt` | FTB Quests 語言檔 |
| `ftbq_inline_snbt` | `.snbt` | FTB Quests 任務檔中的直接文字欄位 |
| `heracles_snbt` | `.snbt` | Heracles（Odyssey Quests）語言檔 |
| `heracles_inline_snbt` | `.snbt` | Heracles 直接文字欄位 |
| `bq_lang` | `.lang` | Better Questing 語言格式（1.12） |
| `kubejs_json` | `.json` | KubeJS 腳本翻譯檔 |

---

## 支援的 Minecraft 版本

1.16.2、1.16.5、1.17、1.17.1、1.18、1.18.2、1.19、1.19.2、1.19.4、1.20、1.20.1、1.20.2、1.20.4、1.20.6、1.21、1.21.1、1.21.3、1.21.4、1.21.5

---

## 輸出結構

```
<模組包資料夾>/
├── mods/               ← 翻譯後的 JAR（原位修改）
├── mods_bak/           ← 原始 JAR 備份
├── config/             ← 翻譯後的任務設定（原位修改）
└── quests_bak/         ← 原始任務設定備份

<專案根目錄>/
├── outputs/
│   └── translation_cache.json   ← 翻譯快取，再次執行時重複使用
└── Failed Items/
    ├── modname__json_lang.txt   ← 重試後仍失敗的字串
    └── ...
```

---

## 常見問題

**Q：ZIP 使用者要怎麼更新？**
- 開啟程式即可。如果 GitHub Release 有新版，更新視窗會出現，按 **自動更新**。
- updater 會保留使用者輸出與備份，但會重建 `.venv` 和本機後端設定，避免依賴衝突。
- Release ZIP 由 GitHub Actions 根據 `v1.5.1` 這類 tag 自動產生。

**Q：掃描找不到任何可翻譯的檔案。**
- 確認選擇的是正確的資料夾，應包含 `mods/` 或 `config/` 子資料夾。
- 若模組包已完全翻譯，所有字串都會被略過。
- 確認至少勾選了一個翻譯選項。

**Q：本機模型服務啟動失敗。**
- 重新執行 `setup_windows.bat` 或 `./setup_unix.sh`。
- 重新初始化前請先關閉翻譯器。Windows 上正在執行的 server 可能鎖住後端檔案。
- NVIDIA CUDA 後端需要 CUDA Toolkit 12.4 或更新版本。cuDNN 不需要。
- 如果 log 只顯示 tensor loading 或 `VirtualLock`/`mlock` warning，通常是模型仍在載入，或舊的後端命令啟用了 memory locking。請重新執行 setup；新產生的 Python 後端預設會關閉 memory locking。
- 查看 `.runtime/llama-server.log`，裡面會有真正的 server 錯誤。

**Q：模型檔案遺失。**
- 確認 LoRA 適配器路徑正確（GUI 設定或 `configs/model.yaml`）。
- 若基礎模型下載失敗，可手動從 HuggingFace 下載，在 `configs/model.yaml` 填入 `base_gguf_path` 後重新執行初始化。

**Q：GPU 沒有被使用 / 翻譯速度很慢。**
- 重新執行初始化，並檢查 `.runtime/backend.json` 內選到的後端。
- 初始化前確認 `configs/model.yaml` 的 `n_gpu_layers` 設為 `-1`（全部層卸載至 GPU）。
- AMD 加速使用 AMD 官方預編譯的 `llama.cpp` binary，支援範圍以 Windows/Linux 為主。

**Q：部分字串回退為英文。**
- 這發生在模型輸出未通過佔位符驗證時（例如翻譯後遺失了 `{0}` 格式代碼）。
- 在 GUI 中增加重試次數，或在 CLI 使用 `--retry N` 參數。
- 失敗項目會記錄於 `Failed Items/`，方便手動檢查。

**Q：翻譯結果輸出在哪裡？**
- **模組 JAR**：翻譯結果直接注入模組 `.jar` 檔案，原始 JAR 備份至 `mods_bak/`。
- **任務設定**：在英文源檔旁邊產生新的語言檔（如 `zh_tw.json`），原始檔備份至 `quests_bak/`。
- **翻譯快取**：儲存於 `outputs/translation_cache.json`，再次執行時自動重複使用。
