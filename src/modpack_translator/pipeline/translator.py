from __future__ import annotations

from pathlib import Path
from typing import Callable

from modpack_translator.config import ModelConfig

_PROJECT_ROOT = Path(__file__).parents[3]


def _resolve_local(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _resolve_base_gguf(cfg: ModelConfig) -> Path:
    if cfg.base_gguf_path:
        p = _resolve_local(cfg.base_gguf_path)
        if not p.exists():
            raise FileNotFoundError(f"base_gguf_path not found: {p}")
        return p

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise RuntimeError(
            "huggingface-hub is required to auto-download the base model.\n"
            "Run: uv add huggingface-hub\n"
            "Or set base_gguf_path in configs/model.yaml to a local GGUF file."
        )

    print(f"Base model not cached locally. Downloading {cfg.base_hf_filename} "
          f"from {cfg.base_hf_repo} (~5 GB, one-time)...")
    path = hf_hub_download(repo_id=cfg.base_hf_repo, filename=cfg.base_hf_filename)
    return Path(path)


def _resolve_lora_gguf(cfg: ModelConfig) -> Path:
    p = _resolve_local(cfg.lora_gguf_path)
    if not p.exists():
        raise FileNotFoundError(
            f"LoRA adapter not found: {p}\n"
            f"Expected at: {_PROJECT_ROOT / 'adapter'}/"
        )
    return p


class GGUFTranslator:
    def __init__(self, cfg: ModelConfig, system_prompt: str) -> None:
        from llama_cpp import Llama

        base_path = _resolve_base_gguf(cfg)
        lora_path = _resolve_lora_gguf(cfg)

        self._llm = Llama(
            model_path=str(base_path),
            lora_path=str(lora_path),
            lora_scale=cfg.lora_scale,
            n_gpu_layers=cfg.n_gpu_layers,
            n_ctx=cfg.n_ctx,
            verbose=cfg.verbose,
        )
        self._system_prompt = system_prompt
        self._cfg = cfg

    def translate(self, text: str, cancel_check: Callable[[], bool] | None = None) -> str:
        """
        翻譯單條字串，使用串流模式逐 token 生成。
        cancel_check 若回傳 True，立即中止並回傳空字串（使後處理驗證失敗，安全回退至原文）。
        """
        chunks: list[str] = []
        stream = self._llm.create_chat_completion(
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user",   "content": text},
            ],
            max_tokens=self._cfg.max_tokens,
            temperature=self._cfg.temperature,
            repeat_penalty=self._cfg.repeat_penalty,
            stream=True,
        )
        for chunk in stream:
            if cancel_check is not None and cancel_check():
                return ""   # 強制後處理失敗 → 安全回退至原文
            delta = chunk["choices"][0]["delta"]
            if "content" in delta:
                chunks.append(delta["content"])
        return "".join(chunks).strip()
