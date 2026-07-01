import modpack_translator.pipeline.translator as tmod
from modpack_translator.config import ModelConfig
from modpack_translator.pipeline.remote_translator import RemoteTranslator


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
    def __init__(self, chunks, capture):
        self._chunks, self._capture = chunks, capture

    def create(self, **kwargs):
        self._capture.update(kwargs)
        return iter([_Chunk(c) for c in self._chunks])


class _Chat:
    def __init__(self, chunks, capture):
        self.completions = _Completions(chunks, capture)


class FakeClient:
    def __init__(self, chunks, capture):
        self.chat = _Chat(chunks, capture)


def test_build_translator_returns_remote_for_remote_mode(monkeypatch):
    import modpack_translator.pipeline.remote_translator as rt
    monkeypatch.setattr(rt, "OpenAI", lambda **kw: FakeClient([], {}))
    cfg = ModelConfig(
        backend_mode="remote",
        remote_base_url="https://api.openai.com/v1",
        remote_model="gpt-4o-mini",
    )
    tr = tmod.build_translator(cfg, "sys")
    assert isinstance(tr, RemoteTranslator)


def test_build_translator_returns_local_for_local_mode(monkeypatch):
    made = {}

    class DummyLocal:
        def __init__(self, cfg, system_prompt):
            made["cfg"] = cfg

    monkeypatch.setattr(tmod, "GGUFTranslator", DummyLocal)
    cfg = ModelConfig(backend_mode="local")
    tr = tmod.build_translator(cfg, "sys")
    assert isinstance(tr, DummyLocal)


def test_local_translator_sends_repeat_penalty():
    # 以 __new__ 略過會啟動本地 server 的 __init__，只測 translate 的參數組裝。
    cap = {}
    tr = tmod.GGUFTranslator.__new__(tmod.GGUFTranslator)
    tr._client = FakeClient(["x"], cap)
    tr._model = "local-model"
    tr._system_prompt = "sys"
    tr._cfg = ModelConfig(repeat_penalty=1.2, max_tokens=10, temperature=0.0)
    out = tr.translate("hello")
    assert out == "x"
    assert cap["extra_body"] == {"repeat_penalty": 1.2}
