import httpx
import pytest
from openai import APIConnectionError

from modpack_translator.pipeline._chat import (
    TranslatorFatalError,
    describe_openai_error,
    normalize_base_url,
    stream_chat,
)


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, chunks, capture, error=None):
        self._chunks = chunks
        self._capture = capture
        self._error = error

    def create(self, **kwargs):
        self._capture.update(kwargs)
        if self._error is not None:
            raise self._error
        return iter([_Chunk(c) for c in self._chunks])


class _Chat:
    def __init__(self, chunks, capture, error=None):
        self.completions = _Completions(chunks, capture, error)


class FakeClient:
    def __init__(self, chunks, capture=None, error=None):
        self.chat = _Chat(chunks, capture if capture is not None else {}, error)


def test_normalize_base_url_strips_v1_and_slash():
    assert normalize_base_url("https://api.openai.com/v1") == "https://api.openai.com"
    assert normalize_base_url("https://api.openai.com/v1/") == "https://api.openai.com"
    assert normalize_base_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api"


def test_stream_chat_joins_chunks_and_strips():
    cap = {}
    client = FakeClient(["Hello", " ", "world", "  "], cap)
    out = stream_chat(client, "m", "sys", "hi", max_tokens=10, temperature=0.0)
    assert out == "Hello world"
    assert cap["model"] == "m"
    assert cap["stream"] is True
    assert "extra_body" not in cap


def test_stream_chat_passes_extra_body_when_given():
    cap = {}
    client = FakeClient(["x"], cap)
    stream_chat(client, "m", "sys", "hi", 10, 0.0, extra_body={"repeat_penalty": 1.1})
    assert cap["extra_body"] == {"repeat_penalty": 1.1}


def test_stream_chat_cancel_returns_empty():
    client = FakeClient(["a", "b", "c"])
    out = stream_chat(client, "m", "sys", "hi", 10, 0.0, cancel_check=lambda: True)
    assert out == ""


def test_stream_chat_wraps_fatal_error():
    err = APIConnectionError(request=httpx.Request("POST", "http://x"))
    client = FakeClient([], error=err)
    with pytest.raises(TranslatorFatalError):
        stream_chat(client, "m", "sys", "hi", 10, 0.0)


def test_describe_openai_error_fallback():
    assert "發生錯誤" in describe_openai_error(ValueError("boom"))
