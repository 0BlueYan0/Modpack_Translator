# Minecraft Modpack Translator v1.5.1

**Language / 語言：** English | [繁體中文](README_zh.md)

[![Ko-fi](https://img.shields.io/badge/Support%20me%20on-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/koudesuk)

---

A tool that automatically translates Minecraft modpack language files from English (`en_us`) to Traditional Chinese (`zh_tw`) using a fine-tuned GGUF model with LoRA adaptation. Supports both a graphical user interface and a command-line interface.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| [Git](https://git-scm.com/downloads) | any | Required to clone the repo |
| [Git LFS](https://git-lfs.com) | any | **Required** — the LoRA adapter (~66 MB) is stored via LFS |
| [uv](https://docs.astral.sh/uv/) | latest | Installs and manages this project's Python runtime |
| GPU (optional) | NVIDIA CUDA or supported AMD ROCm | Strongly recommended; CPU works but is very slow |
| [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) | 12.4 or newer | **Required for NVIDIA CUDA backend**; Game Ready/Studio Driver alone is not enough. cuDNN is not required |
| Free disk space | ~6 GB | ~66 MB for adapter (LFS) + ~5 GB for base model (auto-download) |

---

## Installation

### Step 1 — Install uv

`uv` is a fast Python package manager. Install it once on your machine:

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2 — Install Git LFS

The LoRA adapter is stored in Git LFS. Install it before cloning:

**Windows:** Download the installer from [git-lfs.com](https://git-lfs.com), or:
```powershell
winget install GitHub.GitLFS
```

**macOS:**
```bash
brew install git-lfs
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt install git-lfs
```

Then enable it once for your user account:
```bash
git lfs install
```

### Step 3 — Clone the repository

```bash
git clone <repository-url>
cd Modpack_Translator
```

Git LFS will automatically download the adapter during clone. Verify it downloaded correctly — the file should be **~66 MB**, not a few hundred bytes:

```bash
# Should print ~66 MB
ls -lh adapter/minecraft_translator_gemma4_e4b_lora.gguf   # macOS/Linux
dir adapter\minecraft_translator_gemma4_e4b_lora.gguf       # Windows

# If the file is tiny (a pointer file), run:
git lfs pull
```

### NVIDIA GPU users — Install CUDA Toolkit

If you want to use the CUDA backend, install **CUDA Toolkit 12.4 or newer** before running setup:

```text
https://developer.nvidia.com/cuda-downloads
```

The NVIDIA Game Ready/Studio Driver provides the driver library, but this project's CUDA `llama-cpp-python` wheel also needs CUDA runtime/cuBLAS libraries such as `cudart64_12.dll` and `cublas64_12.dll` on Windows. The setup script checks for these libraries and prints a clear error if they are missing.

cuDNN is **not** required.

### Step 4 — Run the backend setup

The setup script installs uv-managed CPython 3.12, creates `.venv/`, detects your hardware, installs the matching local inference backend, downloads the base model, and writes `.runtime/backend.json`. Users do not need to install Python separately.

**Windows:**
```bat
setup_windows.bat
```

After setup, Windows builds a versioned launcher such as `模組包翻譯器v1.5.1.exe` in the project folder. Double-click it to start the app without opening a terminal. If the launcher is missing, run setup again or build it manually:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows_launcher.ps1
```

**macOS / Linux:**
```bash
./setup_unix.sh
```

Hardware selection is automatic:

| Hardware | Backend |
|---|---|
| NVIDIA | CUDA `llama-cpp-python[server]` wheel |
| AMD Windows/Linux | AMD prebuilt `llama.cpp` / `llama-server` binary |
| CPU only | CPU `llama-cpp-python[server]` wheel |

Close the app before re-running setup. On Windows, a running local model server can lock `.dll` files and prevent backend replacement.

---

## Backend Setup Overrides

Auto-detection should be enough for normal users. To force a backend:

**Windows:**
```bat
setup_windows.bat --backend cuda
setup_windows.bat --backend amd
setup_windows.bat --backend cpu
```

**macOS / Linux:**
```bash
./setup_unix.sh --backend cuda
./setup_unix.sh --backend amd
./setup_unix.sh --backend cpu
```

The application talks to the model through an OpenAI-compatible local HTTP API. You can also start your own compatible server and set `LLAMA_SERVER_URL`, for example `http://127.0.0.1:8888/v1`.

### Using a remote OpenAI-compatible API (optional)

Besides the local model, you can point the translator at any remote OpenAI-compatible
endpoint (OpenAI, OpenRouter, Groq, self-hosted vLLM, etc.).

**GUI:** In "Model settings → Backend mode", choose "Remote API", then fill in the Base URL
(e.g. `https://api.openai.com/v1`), API Key, and model name (e.g. `gpt-4o-mini`). Use
"Test connection" to verify. Settings are saved locally.

**CLI / advanced:** Set `backend_mode: "remote"` plus `remote_base_url` / `remote_api_key` /
`remote_model` in `configs/model.yaml`. Leave any of these blank to fall back to the
environment variables `MODPACK_TRANSLATOR_REMOTE_URL` / `MODPACK_TRANSLATOR_REMOTE_API_KEY` /
`MODPACK_TRANSLATOR_REMOTE_MODEL` — a non-blank config value always takes precedence.

Note: remote mode is billed per provider (modpacks contain many strings), but the translation
cache means re-runs only pay for new strings.

If you change the base model, LoRA adapter, context size, GPU layer count, or backend type in `configs/model.yaml`, run the setup script again so `.runtime/backend.json` is regenerated.

---

## Configuration Files

### `configs/model.yaml`

```yaml
model:
  base_gguf_path: ""                              # Leave blank to auto-download
  base_hf_repo: "unsloth/gemma-4-E4B-it-GGUF"
  base_hf_filename: "gemma-4-E4B-it-Q4_K_M.gguf"
  lora_gguf_path: "adapter/minecraft_translator_gemma4_e4b_lora.gguf"
  lora_scale: 1.0
  n_gpu_layers: -1     # -1 = all GPU, 0 = CPU only
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

Contains the language code, display name, and system prompt for the translation model. Do not modify unless you are adding support for a different target language.

---

## GUI Usage

Launch the graphical interface:

```bash
uv run python main.py
```

On Windows, users can also double-click the versioned launcher EXE, such as `模組包翻譯器v1.5.1.exe`. It checks that setup has been run, launches `uv run python main.py` in the background, and writes launcher errors to `.runtime/launcher.log`.

On startup, the app checks the latest GitHub Release in the background. If a newer release package is available, it shows an update dialog; if there is no update, it shows nothing. Auto-update downloads the release ZIP, verifies its SHA256 file when present, applies the new source files, removes the old `.venv` and stale local backend runtime files, runs setup again, and then restarts the app.

**Step-by-step workflow:**

1. **Modpack Folder** — Click "瀏覽…" to select your modpack instance directory (the folder containing `mods/`, `config/`, etc.).
2. **Model Settings** — The normal setup flow already configured the local model server. Only change these fields if you also regenerate the backend setup.
3. **Options** — Check "翻譯模組 (.jar)" and/or "翻譯任務書". Set retry count (default: 3).
4. **Scan** — Click "🔍 掃描模組包". The result panel shows the number of targets and sample strings.
5. **Translate** — Click "▶ 開始翻譯". The progress bar shows percentage, speed, elapsed time, and ETA.
6. **Done** — When complete, the progress bar turns green and the button shows "✓ 完成".

**Original files are always backed up:**
- Mod JARs → `mods_bak/`
- Quest configs → `quests_bak/`

**Failed items** (strings that could not be translated after all retries) are written to `Failed Items/<mod_name>.txt` for review. If no items fail, this folder is not created.

---

## CLI Usage

```bash
uv run python scripts/translate_modpack.py --modpack <path> [options]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--modpack PATH` | (required) | Path to the modpack instance folder |
| `--language FILE` | `configs/languages/zh_tw.yaml` | Language config file |
| `--model-config FILE` | `configs/model.yaml` | Model config file |
| `--paths-config FILE` | `configs/paths.yaml` | Paths config file |
| `--dry-run` | false | Scan only, no translation |
| `--skip-mods` | false | Skip mod JAR scanning |
| `--skip-quests` | false | Skip quest config scanning |
| `--max-steps N` | -1 (all) | Limit to first N targets (for testing) |
| `--retry N` | 0 | Retry count per string when postprocessor rejects output |

### Examples

```bash
# Dry run to preview what will be translated
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --dry-run

# Full translation with 3 retries per failed string
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --retry 3

# Translate quest files only
uv run python scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --skip-mods --retry 2
```

---

## Supported File Formats

| Format Key | Extension | Description |
|---|---|---|
| `json_lang` | `.json` | Standard mod language file (`assets/<mod>/lang/en_us.json`) |
| `legacy_lang` | `.lang` | Pre-1.13 mod language file (`en_us.lang`) |
| `patchouli_json` | `.json` | Patchouli guidebook pages |
| `ftbq_snbt` | `.snbt` | FTB Quests language files |
| `ftbq_inline_snbt` | `.snbt` | FTB Quests direct text fields in quest files |
| `heracles_snbt` | `.snbt` | Heracles (Odyssey Quests) language files |
| `heracles_inline_snbt` | `.snbt` | Heracles inline text fields |
| `bq_lang` | `.lang` | Better Questing language format (1.12) |
| `kubejs_json` | `.json` | KubeJS script translation files |

---

## Supported Minecraft Versions

1.16.2, 1.16.5, 1.17, 1.17.1, 1.18, 1.18.2, 1.19, 1.19.2, 1.19.4, 1.20, 1.20.1, 1.20.2, 1.20.4, 1.20.6, 1.21, 1.21.1, 1.21.3, 1.21.4, 1.21.5

---

## Output Structure

```
<modpack-folder>/
├── mods/               ← translated JARs (in-place)
├── mods_bak/           ← original JAR backups
├── config/             ← translated quest configs (in-place)
└── quests_bak/         ← original quest config backups

<project-root>/
├── outputs/
│   └── translation_cache.json   ← reused on subsequent runs
└── Failed Items/
    ├── modname__json_lang.txt   ← strings that failed after all retries
    └── ...
```

---

## FAQ

**Q: How do ZIP users update the app?**
- Open the app. If a newer GitHub Release exists, click **Auto update** in the update dialog.
- The updater preserves user outputs and backups, but rebuilds `.venv` and the local backend setup to avoid dependency conflicts.
- Release ZIPs are generated by GitHub Actions from tags such as `v1.5.1`.

**Q: Scan finds 0 translatable files.**
- Make sure you selected the correct folder. It should be the instance root containing `mods/` or `config/`.
- If the modpack was already translated, all strings will be skipped.
- Check that at least one translation option is checked.

**Q: Local model server fails to start.**
- Re-run `setup_windows.bat` or `./setup_unix.sh`.
- Close the app before re-running setup. A running server can lock backend files on Windows.
- For NVIDIA CUDA backend, install CUDA Toolkit 12.4 or newer. cuDNN is not required.
- If the log only shows tensor loading or a `VirtualLock`/`mlock` warning, the model is usually still loading or an old backend command enabled memory locking. Re-run setup; generated Python backends disable memory locking by default.
- Check `.runtime/llama-server.log` for the real server error.

**Q: Model files are missing.**
- Verify the LoRA adapter path in the GUI or `configs/model.yaml`.
- If the base model download fails, download it manually from HuggingFace, set `base_gguf_path` in `configs/model.yaml`, then run setup again.

**Q: GPU is not being used / translation is slow.**
- Run setup again and check the selected backend in `.runtime/backend.json`.
- Make sure `n_gpu_layers` is set to `-1` in `configs/model.yaml` before running setup.
- AMD acceleration uses AMD's prebuilt `llama.cpp` binaries on supported Windows/Linux systems.

**Q: Some strings fall back to English.**
- This happens when the model output fails the placeholder validation (e.g., a `{0}` format code is missing from the translation).
- Increase the retry count in the GUI or with `--retry N` on the CLI.
- Failed items are logged to `Failed Items/` for manual review.

**Q: Where is the translated output?**
- **Mod JARs**: Translations are injected directly into the mod `.jar` files. Original JARs are backed up to `mods_bak/`.
- **Quest configs**: A new language file (e.g., `zh_tw.json`) is written next to the English source. Originals are backed up to `quests_bak/`.
- **Translation cache**: Stored at `outputs/translation_cache.json` for reuse on subsequent runs.
