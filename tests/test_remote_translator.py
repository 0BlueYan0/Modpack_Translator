import httpx
from openai import APIConnectionError, AuthenticationError

import modpack_translator.pipeline.remote_translator as rt
from modpack_translator.config import ModelConfig


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
        self._chunks, self._capture, self._error = chunks, capture, error

    def create(self, **kwargs):
        self._capture.update(kwargs)
        if self._error is not None:
            raise self._error
        return iter([_Chunk(c) for c in self._chunks])


class _Chat:
    def __init__(self, chunks, capture, error):
        self.completions = _Completions(chunks, capture, error)


class FakeClient:
    def __init__(self, chunks=(), capture=None, error=None):
        self.chat = _Chat(list(chunks), capture if capture is not None else {}, error)


def test_remote_translate_omits_repeat_penalty(monkeypatch):
    cap = {}
    monkeypatch.setattr(rt, "OpenAI", lambda **kw: FakeClient(["你好"], cap))
    cfg = ModelConfig(
        backend_mode="remote",
        remote_base_url="https://api.openai.com/v1",
        remote_api_key="sk-test",
        remote_model="gpt-4o-mini",
    )
    tr = rt.RemoteTranslator(cfg, "sys")
    out = tr.translate("hello")
    assert out == "你好"
    assert "extra_body" not in cap
    assert cap["model"] == "gpt-4o-mini"


def test_remote_cfg_beats_env_and_env_fills_blank(monkeypatch):
    # cfg.remote_model 非空時，即使環境變數也有設定，cfg 值仍應勝出。
    cap_cfg = {}
    monkeypatch.setattr(rt, "OpenAI", lambda **kw: FakeClient(["x"], cap_cfg))
    monkeypatch.setenv("MODPACK_TRANSLATOR_REMOTE_MODEL", "env-model")
    cfg = ModelConfig(backend_mode="remote", remote_base_url="http://x/v1", remote_model="cfg-model")
    tr = rt.RemoteTranslator(cfg, "sys")
    tr.translate("hi")
    assert cap_cfg["model"] == "cfg-model"
    monkeypatch.delenv("MODPACK_TRANSLATOR_REMOTE_MODEL", raising=False)

    # cfg.remote_model 為空字串時，環境變數作為備援被採用。
    cap_env = {}
    monkeypatch.setattr(rt, "OpenAI", lambda **kw: FakeClient(["y"], cap_env))
    monkeypatch.setenv("MODPACK_TRANSLATOR_REMOTE_MODEL", "env-model")
    cfg_blank = ModelConfig(backend_mode="remote", remote_base_url="http://x/v1", remote_model="")
    tr2 = rt.RemoteTranslator(cfg_blank, "sys")
    tr2.translate("hi")
    assert cap_env["model"] == "env-model"
    monkeypatch.delenv("MODPACK_TRANSLATOR_REMOTE_MODEL", raising=False)


def test_remote_init_requires_url_and_model(monkeypatch):
    monkeypatch.setattr(rt, "OpenAI", lambda **kw: FakeClient())
    import pytest
    with pytest.raises(rt.TranslatorFatalError):
        rt.RemoteTranslator(ModelConfig(backend_mode="remote", remote_model="m"), "sys")
    with pytest.raises(rt.TranslatorFatalError):
        rt.RemoteTranslator(ModelConfig(backend_mode="remote", remote_base_url="http://x/v1"), "sys")


def test_test_remote_connection_success(monkeypatch):
    cap = {}
    monkeypatch.setattr(rt, "OpenAI", lambda **kw: FakeClient(["ok"], cap))
    ok, msg = rt.test_remote_connection("https://api.openai.com/v1", "sk-test", "gpt-4o-mini")
    assert ok is True
    assert msg == "連線成功"
    assert cap["max_tokens"] == 1
    assert cap["stream"] is False


def test_test_remote_connection_connection_error(monkeypatch):
    err = APIConnectionError(request=httpx.Request("POST", "http://x"))
    monkeypatch.setattr(rt, "OpenAI", lambda **kw: FakeClient(error=err))
    ok, msg = rt.test_remote_connection("http://x/v1", "k", "m")
    assert ok is False
    assert "無法連線" in msg


def test_test_remote_connection_missing_fields():
    ok, msg = rt.test_remote_connection("", "k", "m")
    assert ok is False
    ok2, msg2 = rt.test_remote_connection("http://x/v1", "k", "")
    assert ok2 is False
