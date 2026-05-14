# Minecraft Modpack Translator v1.0.0

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
| [Python](https://www.python.org/downloads/) | 3.10 or higher | |
| [uv](https://docs.astral.sh/uv/) | latest | Python package manager used by this project |
| NVIDIA GPU (optional) | CUDA 12.4+ | Strongly recommended; CPU works but is very slow |
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

### Step 4 — Install Python dependencies

```bash
uv sync
```

This creates a `.venv/` folder and installs all required packages. The base model (~5 GB) is **not** downloaded here — it is downloaded automatically on the **first translation run**.

> **Note:** By default, `uv sync` installs the CPU-only build of `llama-cpp-python`. See [GPU Setup](#gpu-setup-optional-but-recommended) below to enable GPU acceleration.

---

## GPU Setup (Optional but Recommended)

By default, the CPU build of `llama-cpp-python` is installed. For GPU-accelerated inference, install a pre-built CUDA wheel:

**Windows (CUDA 12.4):**
```bash
uv pip install "https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.23-cu124/llama_cpp_python-0.3.23-py3-none-win_amd64.whl" --force-reinstall
```

**Linux / WSL (CUDA 12.5):**
```bash
uv pip install "https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.23-cu125/llama_cpp_python-0.3.23-py3-none-linux_x86_64.whl" --force-reinstall
```

For other CUDA versions, browse the [llama-cpp-python releases](https://github.com/abetlen/llama-cpp-python/releases) page and select the wheel matching your CUDA version (tag format: `v0.3.23-cu<version>`).

**CPU-only fallback (no GPU required, slower):**
```bash
uv pip install llama-cpp-python --no-binary llama-cpp-python --force-reinstall
```
Then set `n_gpu_layers: 0` in `configs/model.yaml`.

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
uv run main.py
```

**Step-by-step workflow:**

1. **Modpack Folder** — Click "瀏覽…" to select your modpack instance directory (the folder containing `mods/`, `config/`, etc.).
2. **Model Settings** — Leave "Base Model" blank for auto-download. The LoRA adapter path is pre-filled.
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
uv run scripts/translate_modpack.py --modpack <path> [options]
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
uv run scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --dry-run

# Full translation with 3 retries per failed string
uv run scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --retry 3

# Translate quest files only
uv run scripts/translate_modpack.py --modpack "C:/CurseForge/Instances/ATM10" --skip-mods --retry 2
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

**Q: Scan finds 0 translatable files.**
- Make sure you selected the correct folder. It should be the instance root containing `mods/` or `config/`.
- If the modpack was already translated, all strings will be skipped.
- Check that at least one translation option is checked.

**Q: Model fails to load.**
- Verify the LoRA adapter path in the GUI or `configs/model.yaml`.
- If the base model download fails, download it manually from HuggingFace and set `base_gguf_path` in the config.

**Q: GPU is not being used / translation is slow.**
- Install the CUDA wheel for your GPU (see GPU Setup above).
- Make sure `n_gpu_layers` is set to `-1` (all layers on GPU).

**Q: Some strings fall back to English.**
- This happens when the model output fails the placeholder validation (e.g., a `{0}` format code is missing from the translation).
- Increase the retry count in the GUI or with `--retry N` on the CLI.
- Failed items are logged to `Failed Items/` for manual review.

**Q: Where is the translated output?**
- **Mod JARs**: Translations are injected directly into the mod `.jar` files. Original JARs are backed up to `mods_bak/`.
- **Quest configs**: A new language file (e.g., `zh_tw.json`) is written next to the English source. Originals are backed up to `quests_bak/`.
- **Translation cache**: Stored at `outputs/translation_cache.json` for reuse on subsequent runs.
