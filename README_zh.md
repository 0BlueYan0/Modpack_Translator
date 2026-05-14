# Minecraft模組包翻譯器 v1.0.0

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
| [Python](https://www.python.org/downloads/) | 3.10 或以上 | |
| [uv](https://docs.astral.sh/uv/) | 最新版 | 本專案使用的 Python 套件管理器 |
| NVIDIA GPU（可選） | 建議 CUDA 12.4 以上 | 強烈建議；純 CPU 可用但速度非常慢 |
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

### 第四步 — 安裝 Python 相依套件

```bash
uv sync
```

此指令會建立 `.venv/` 虛擬環境並安裝所有套件。基礎模型（約 5 GB）**不在此時下載**，會在**首次翻譯執行時**自動下載。

> **注意：** `uv sync` 預設安裝 CPU 版的 `llama-cpp-python`。若要啟用 GPU 加速，請參閱下方的 [GPU 加速設定](#gpu-加速設定可選強烈建議)。

---

## GPU 加速設定（可選，強烈建議）

預設安裝的是 CPU 版 `llama-cpp-python`。若要使用 GPU 加速推理，請安裝預先編譯的 CUDA 安裝包：

**Windows（CUDA 12.4）：**
```bash
uv pip install "https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.23-cu124/llama_cpp_python-0.3.23-py3-none-win_amd64.whl" --force-reinstall
```

**Linux / WSL（CUDA 12.5）：**
```bash
uv pip install "https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.23-cu125/llama_cpp_python-0.3.23-py3-none-linux_x86_64.whl" --force-reinstall
```

其他 CUDA 版本請至 [llama-cpp-python releases](https://github.com/abetlen/llama-cpp-python/releases) 選擇對應標籤（格式：`v0.3.23-cu<版本號>`）。

**僅 CPU 回退（不需要 GPU，速度較慢）：**
```bash
uv pip install llama-cpp-python --no-binary llama-cpp-python --force-reinstall
```
並將 `configs/model.yaml` 中的 `n_gpu_layers` 設為 `0`。

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
uv run main.py
```

**操作步驟：**

1. **模組包資料夾** — 點擊「瀏覽…」選擇模組包實例目錄（包含 `mods/`、`config/` 的資料夾）。
2. **模型設定** — 「基礎模型」留空可自動下載；LoRA 適配器路徑已預先填入。
3. **選項** — 勾選「翻譯模組 (.jar)」或「翻譯任務書」，並設定重試次數（預設 3）。
4. **掃描** — 點擊「🔍 掃描模組包」，掃描結果面板顯示目標數量與樣本字串。
5. **翻譯** — 點擊「▶ 開始翻譯」，進度條顯示百分比、速度、已用時間及預計剩餘時間。
6. **完成** — 翻譯完成後，進度條變綠，按鈕顯示「✓ 完成」。

**原始檔案備份位置：**
- 模組 JAR → `mods_bak/`
- 任務設定 → `quests_bak/`

**失敗項目**（重試後仍無法翻譯的字串）會寫入 `Failed Items/<模組名稱>.txt`，供使用者檢查。若無失敗項目，此資料夾不會被建立。

---

## CLI 使用方法

```bash
uv run scripts/translate_modpack.py --modpack <路徑> [選項]
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
uv run scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --dry-run

# 完整翻譯，失敗時最多重試 3 次
uv run scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --retry 3

# 僅翻譯任務書
uv run scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --skip-mods --retry 2
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

**Q：掃描找不到任何可翻譯的檔案。**
- 確認選擇的是正確的資料夾，應包含 `mods/` 或 `config/` 子資料夾。
- 若模組包已完全翻譯，所有字串都會被略過。
- 確認至少勾選了一個翻譯選項。

**Q：模型載入失敗。**
- 確認 LoRA 適配器路徑正確（GUI 設定或 `configs/model.yaml`）。
- 若基礎模型下載失敗，可手動從 HuggingFace 下載後，在設定中填入 `base_gguf_path`。

**Q：GPU 沒有被使用 / 翻譯速度很慢。**
- 安裝適合您 GPU 的 CUDA 安裝包（請參閱上方 GPU 加速設定）。
- 確認 `n_gpu_layers` 設為 `-1`（全部層卸載至 GPU）。

**Q：部分字串回退為英文。**
- 這發生在模型輸出未通過佔位符驗證時（例如翻譯後遺失了 `{0}` 格式代碼）。
- 在 GUI 中增加重試次數，或在 CLI 使用 `--retry N` 參數。
- 失敗項目會記錄於 `Failed Items/`，方便手動檢查。

**Q：翻譯結果輸出在哪裡？**
- **模組 JAR**：翻譯結果直接注入模組 `.jar` 檔案，原始 JAR 備份至 `mods_bak/`。
- **任務設定**：在英文源檔旁邊產生新的語言檔（如 `zh_tw.json`），原始檔備份至 `quests_bak/`。
- **翻譯快取**：儲存於 `outputs/translation_cache.json`，再次執行時自動重複使用。
