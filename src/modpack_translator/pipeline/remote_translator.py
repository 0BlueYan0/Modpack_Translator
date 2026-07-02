from __future__ import annotations

import os
from typing import Callable

from openai import OpenAI

from modpack_translator.config import ModelConfig
from modpack_translator.pipeline._chat import (
    TranslatorFatalError,
    describe_openai_error,
    normalize_base_url,
    stream_chat,
)
from modpack_translator.pipeline.glossary import Glossary, augment_prompt


def resolve_remote_settings(cfg: ModelConfig) -> tuple[str, str, str]:
    """解析遠端連線設定，回傳 (base_url, api_key, model)。

    明確設定（GUI QSettings 經 _build_cfg 注入，或 model.yaml）優先；
    環境變數僅在對應欄位留空時作為備援填補。
    缺 base_url／model 時拋 TranslatorFatalError。
    """
    base_url = cfg.remote_base_url or os.getenv("MODPACK_TRANSLATOR_REMOTE_URL", "")
    api_key = cfg.remote_api_key or os.getenv("MODPACK_TRANSLATOR_REMOTE_API_KEY", "")
    model = cfg.remote_model or os.getenv("MODPACK_TRANSLATOR_REMOTE_MODEL", "")

    if not base_url:
        raise TranslatorFatalError("未設定遠端 API Base URL。")
    if not model:
        raise TranslatorFatalError("未設定遠端模型名稱。")
    return base_url, api_key, model


class RemoteTranslator:
    """對遠端 OpenAI 相容 API 做串流翻譯。無本地 server 生命週期，close() 為 no-op。"""

    # 類別層級預設：測試以 __new__ 跳過 __init__ 時仍可安全讀取
    glossary: Glossary | None = None

    def __init__(
        self,
        cfg: ModelConfig,
        system_prompt: str,
        glossary: Glossary | None = None,
    ) -> None:
        base_url, api_key, model = resolve_remote_settings(cfg)

        self._cfg = cfg
        self._model = model
        self._system_prompt = system_prompt
        self.glossary = glossary  # public：runner 以 getattr 取用做整串短路
        self._client = OpenAI(
            base_url=f"{normalize_base_url(base_url)}/v1",
            api_key=api_key or "not-needed",
            timeout=cfg.remote_timeout_s,
            max_retries=0,  # 重試由分段重試階梯管理，不疊 SDK 隱性重試
        )

    def translate(self, text: str, cancel_check: Callable[[], bool] | None = None) -> str:
        return stream_chat(
            self._client,
            self._model,
            augment_prompt(self._system_prompt, self.glossary, [text]),
            text,
            max_tokens=self._cfg.max_tokens,
            temperature=self._cfg.temperature,
            extra_body=None,  # 遠端只送標準 OpenAI 參數，不送 repeat_penalty
            cancel_check=cancel_check,
        )

    def close(self) -> None:
        pass

    def __enter__(self) -> "RemoteTranslator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def test_remote_connection(
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """用 1-token 的 chat completion 探測，一次驗證 URL／金鑰／模型名。

    回傳 (ok, 訊息)。訊息為繁中，可直接顯示於 GUI。
    """
    if not base_url:
        return False, "請先填寫 Base URL"
    if not model:
        return False, "請先填寫模型名稱"
    try:
        client = OpenAI(
            base_url=f"{normalize_base_url(base_url)}/v1",
            api_key=api_key or "not-needed",
            timeout=timeout,
        )
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            stream=False,
        )
        return True, "連線成功"
    except Exception as exc:  # noqa: BLE001 — 測試連線要吞下所有例外轉成訊息
        return False, describe_openai_error(exc)
