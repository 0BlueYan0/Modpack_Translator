from __future__ import annotations

from typing import Callable

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
)


class TranslatorFatalError(RuntimeError):
    """系統性錯誤（金鑰／連線／找不到模型等），整趟翻譯應直接中止而非逐條略過。"""


# 這幾類錯誤只要發生一次，之後每一條請求都會同樣失敗 → 視為致命，直接中止。
FATAL_OPENAI_ERRORS: tuple[type[Exception], ...] = (
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    APIConnectionError,
)


def normalize_base_url(url: str) -> str:
    url = url.rstrip("/")
    return url[:-3].rstrip("/") if url.endswith("/v1") else url


def describe_openai_error(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return "API 金鑰錯誤或未授權"
    if isinstance(exc, PermissionDeniedError):
        return "無權限存取此端點或模型"
    if isinstance(exc, NotFoundError):
        return "找不到模型或端點，請確認模型名稱與網址"
    if isinstance(exc, APIConnectionError):
        return "無法連線，請確認網址與網路連線"
    if isinstance(exc, APIStatusError):
        return f"伺服器回應錯誤（HTTP {exc.status_code}）：{getattr(exc, 'message', str(exc))}"
    return f"發生錯誤：{exc}"


def stream_chat(
    client,
    model: str,
    system_prompt: str,
    text: str,
    max_tokens: int,
    temperature: float,
    extra_body: dict | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """串流 chat completion，逐 token 累積後回傳。

    cancel_check() 回 True 立即中止並回傳空字串（使後處理驗證失敗，安全回退至原文）。
    遇到 FATAL_OPENAI_ERRORS 轉拋 TranslatorFatalError，讓呼叫端中止整趟。
    """
    kwargs: dict = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
    )
    if extra_body:
        kwargs["extra_body"] = extra_body

    try:
        stream = client.chat.completions.create(**kwargs)
        chunks: list[str] = []
        for chunk in stream:
            if cancel_check is not None and cancel_check():
                return ""
            delta = chunk.choices[0].delta
            if delta.content:
                chunks.append(delta.content)
        return "".join(chunks).strip()
    except FATAL_OPENAI_ERRORS as exc:
        raise TranslatorFatalError(describe_openai_error(exc)) from exc
