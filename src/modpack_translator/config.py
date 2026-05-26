from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

MC_PACK_FORMAT: dict[str, int] = {
    "1.16.2": 6,  "1.16.5": 6,
    "1.17":   7,  "1.17.1": 7,
    "1.18":   8,  "1.18.2": 8,
    "1.19":   9,  "1.19.2": 12, "1.19.4": 13,
    "1.20":  15,  "1.20.1": 15, "1.20.2": 18,
    "1.20.4": 22, "1.20.6": 32,
    "1.21":  34,  "1.21.1": 34,
    "1.21.3": 42, "1.21.4": 46, "1.21.5": 55,
}


def mc_version_to_pack_format(version: str) -> int:
    if version in MC_PACK_FORMAT:
        return MC_PACK_FORMAT[version]
    known = ", ".join(sorted(MC_PACK_FORMAT))
    raise ValueError(f"Unknown Minecraft version '{version}'. Known versions: {known}")


class ModelConfig(BaseModel):
    # Base model — downloaded automatically from HuggingFace on first run (~5 GB)
    base_gguf_path: str = ""                            # override with local path if already downloaded
    base_hf_repo: str = "unsloth/gemma-4-E4B-it-GGUF"
    base_hf_filename: str = "gemma-4-E4B-it-Q4_K_M.gguf"

    # LoRA adapter exported from training (66 MB)
    lora_gguf_path: str = "adapter/minecraft_translator_gemma4_e4b_lora.gguf"
    lora_scale: float = 1.0

    n_gpu_layers: int = -1
    n_ctx: int = 2048
    max_tokens: int = 512
    temperature: float = 0.05
    repeat_penalty: float = 1.1
    verbose: bool = False
    server_url: str = "http://127.0.0.1:8080/v1"
    server_api_key: str = "llama.cpp"
    server_model: str = "local-model"
    auto_start_server: bool = True
    server_ready_timeout: int = 180
    server_start_command: str | list[str] | None = None


class PathsConfig(BaseModel):
    output_root: Path
    resource_pack_dir: Path
    translation_cache: Path

    @field_validator("*", mode="before")
    @classmethod
    def expand_path(cls, v):
        if isinstance(v, str):
            return Path(v).expanduser()
        return v

    def create_output_dirs(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.resource_pack_dir.mkdir(parents=True, exist_ok=True)
        self.translation_cache.parent.mkdir(parents=True, exist_ok=True)


class LanguageConfig(BaseModel):
    code: str
    display_name: str
    system_prompt: str


class AppConfig(BaseModel):
    model: ModelConfig
    paths: PathsConfig
    language: LanguageConfig


def _load_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(
    model_yaml: str | Path,
    paths_yaml: str | Path,
    language_yaml: str | Path,
) -> AppConfig:
    model_raw    = _load_yaml(model_yaml)
    paths_raw    = _load_yaml(paths_yaml)
    language_raw = _load_yaml(language_yaml)

    return AppConfig(
        model=ModelConfig(**model_raw["model"]),
        paths=PathsConfig(**paths_raw["paths"]),
        language=LanguageConfig(**language_raw["language"]),
    )
