# 遠端 OpenAI 相容 API 支援 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓翻譯器除了現有本地 llama.cpp 模型外，也能使用遠端 OpenAI 相容 API，本地／遠端可在 GUI 切換。

**Architecture:** 抽出共用的 OpenAI 串流翻譯迴圈 `stream_chat`；保留本地 `GGUFTranslator`，新增精簡的 `RemoteTranslator`；用工廠 `build_translator(cfg, prompt)` 依 `backend_mode` 回傳對應物件（介面一致：`translate()` / `close()` / context manager）。GUI 加「本地／遠端」切換與遠端欄位，設定存 QSettings。

**Tech Stack:** Python ≥3.10、`openai>=1.0`（已是相依）、PySide6（已是相依）、pydantic v2、uv、pytest（新增 dev 相依）。

## Global Constraints

- Python 版本下限：`requires-python = ">=3.10"`（勿使用更新語法）。
- 不新增執行期相依；唯一新增的是 **dev 相依 `pytest`**。
- 既有的 `server_url / server_api_key / server_model` 欄位維持不動（本地 server 用）；遠端一律用**新的** `remote_*` 欄位，避免被 `.runtime/backend.json` 蓋掉。
- 遠端**只送標準 OpenAI 參數**；`repeat_penalty` 僅限本地模式。
- 程式碼識別字用英文；註解沿用專案既有的繁體中文風格。
- 測試指令一律：`uv run pytest tests/ -v`。新增依賴：`uv add --dev pytest`。
- 遠端欄位讀取優先序：GUI QSettings（經 `_build_cfg` 注入 `cfg.model`）> 環境變數 `MODPACK_TRANSLATOR_REMOTE_URL / _API_KEY / _MODEL` > `configs/model.yaml`。

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `src/modpack_translator/config.py` | `ModelConfig` 新增 `backend_mode` 與 `remote_*` 欄位 |
| `configs/model.yaml` | 新增遠端欄位與註解 |
| `src/modpack_translator/pipeline/_chat.py`（新） | 共用 `stream_chat`、`normalize_base_url`、`describe_openai_error`、`TranslatorFatalError`、`FATAL_OPENAI_ERRORS` |
| `src/modpack_translator/pipeline/remote_translator.py`（新） | `RemoteTranslator`、`test_remote_connection` |
| `src/modpack_translator/pipeline/translator.py` | 改用 `_chat`；`GGUFTranslator.translate` 走 `stream_chat`；新增 `build_translator` 工廠 |
| `src/modpack_translator/gui/worker.py` | 改用工廠、模式感知連線訊息、致命錯誤中止 |
| `scripts/translate_modpack.py` | 改用工廠、致命錯誤中止 |
| `src/modpack_translator/gui/main_window.py` | 後端模式切換 UI、遠端欄位、測試連線、`_build_cfg` / `_start_translation` 調整 |
| `tests/`（新） | 單元測試 |
| `pyproject.toml` | dev 相依加 `pytest`、pytest 設定 |
| `README.md` / `README_zh.md` | 遠端 API 使用說明 |

---

## Task 1: 測試基礎建設 + ModelConfig 遠端欄位

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/modpack_translator/config.py:27-48`（`ModelConfig`）
- Modify: `configs/model.yaml`（於 `server_ready_timeout` 之後新增）
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `ModelConfig` 新增欄位 `backend_mode: str = "local"`、`remote_base_url: str = ""`、`remote_api_key: str = ""`、`remote_model: str = ""`。

- [ ] **Step 1: 加入 pytest dev 相依與設定**

Run:
```bash
uv add --dev pytest
```

接著在 `pyproject.toml` 末端加入 pytest 設定（讓 `src/` 進入匯入路徑，因為專案未安裝成套件）：
```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: 寫失敗測試**

Create `tests/test_config.py`:
```python
from modpack_translator.config import ModelConfig


def test_model_config_remote_defaults():
    c = ModelConfig()
    assert c.backend_mode == "local"
    assert c.remote_base_url == ""
    assert c.remote_api_key == ""
    assert c.remote_model == ""


def test_model_config_accepts_remote():
    c = ModelConfig(
        backend_mode="remote",
        remote_base_url="https://api.openai.com/v1",
        remote_api_key="sk-test",
        remote_model="gpt-4o-mini",
    )
    assert c.backend_mode == "remote"
    assert c.remote_base_url == "https://api.openai.com/v1"
    assert c.remote_api_key == "sk-test"
    assert c.remote_model == "gpt-4o-mini"
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL（`ModelConfig` 目前無 `backend_mode` 等欄位，`test_model_config_remote_defaults` 觸發 `AttributeError`；`test_model_config_accepts_remote` 因 pydantic 拒絕未知欄位而 `ValidationError`）。

- [ ] **Step 4: 在 `ModelConfig` 新增欄位**

於 `src/modpack_translator/config.py` 的 `server_start_command: str | list[str] | None = None`（第 48 行）之後加入：
```python
    # ── 遠端 OpenAI 相容 API（可選）──────────────────────────
    backend_mode: str = "local"          # "local" | "remote"
    remote_base_url: str = ""            # 例如 https://api.openai.com/v1
    remote_api_key: str = ""             # 例如 sk-...
    remote_model: str = ""               # 例如 gpt-4o-mini
```

- [ ] **Step 5: 同步 `configs/model.yaml`**

於 `configs/model.yaml` 的 `server_ready_timeout: 600` 之後加入：
```yaml

  # ── 遠端 OpenAI 相容 API（可選）───────────────────────────────
  # backend_mode: "local" 使用上面的本地 llama.cpp server；
  #               "remote" 改用下列遠端 OpenAI 相容端點（OpenAI / OpenRouter / Groq / 自架 vLLM…）。
  # GUI 會以「模型設定 → 後端模式」的選擇覆寫這裡；CLI 則直接讀這裡。
  # 也可用環境變數覆寫：MODPACK_TRANSLATOR_REMOTE_URL / _API_KEY / _MODEL
  backend_mode: "local"
  remote_base_url: ""      # 例如 https://api.openai.com/v1
  remote_api_key: ""       # 例如 sk-...
  remote_model: ""         # 例如 gpt-4o-mini
```

- [ ] **Step 6: 執行測試確認通過**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/modpack_translator/config.py configs/model.yaml tests/test_config.py
git commit -m "feat: 新增遠端 API 設定欄位與 pytest 測試基礎"
```

---

## Task 2: 共用串流 helper `_chat.py`

**Files:**
- Create: `src/modpack_translator/pipeline/_chat.py`
- Create: `tests/test_chat.py`

**Interfaces:**
- Produces:
  - `class TranslatorFatalError(RuntimeError)` — 系統性錯誤（金鑰／連線／找不到模型），呼叫端應中止整趟。
  - `FATAL_OPENAI_ERRORS: tuple[type[Exception], ...]`
  - `def normalize_base_url(url: str) -> str` — 去尾斜線並去掉結尾 `/v1`。
  - `def describe_openai_error(exc: Exception) -> str` — 轉繁中訊息。
  - `def stream_chat(client, model: str, system_prompt: str, text: str, max_tokens: int, temperature: float, extra_body: dict | None = None, cancel_check=None) -> str` — 串流 chat completion，逐 token 累積；`cancel_check()` 回 True 立即回傳 `""`；遇 `FATAL_OPENAI_ERRORS` 拋 `TranslatorFatalError`。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_chat.py`:
```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_chat.py -v`
Expected: FAIL（`ModuleNotFoundError: modpack_translator.pipeline._chat`）。

- [ ] **Step 3: 實作 `_chat.py`**

Create `src/modpack_translator/pipeline/_chat.py`:
```python
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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_chat.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/_chat.py tests/test_chat.py
git commit -m "feat: 新增共用 OpenAI 串流 helper stream_chat"
```

---

## Task 3: `RemoteTranslator` 與 `test_remote_connection`

**Files:**
- Create: `src/modpack_translator/pipeline/remote_translator.py`
- Create: `tests/test_remote_translator.py`

**Interfaces:**
- Consumes: `modpack_translator.pipeline._chat`（`stream_chat`、`normalize_base_url`、`describe_openai_error`、`TranslatorFatalError`）；`modpack_translator.config.ModelConfig`。
- Produces:
  - `class RemoteTranslator` — `__init__(self, cfg: ModelConfig, system_prompt: str)`、`translate(self, text: str, cancel_check=None) -> str`（`extra_body=None`，不送 `repeat_penalty`）、`close(self) -> None`（no-op）、`__enter__` / `__exit__`。缺 `remote_base_url` 或 `remote_model` 時於 `__init__` 拋 `TranslatorFatalError`。
  - `def test_remote_connection(base_url: str, api_key: str, model: str, timeout: float = 15.0) -> tuple[bool, str]`。
  - 模組層級 `OpenAI`（`from openai import OpenAI`），供測試以 monkeypatch 替換。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_remote_translator.py`:
```python
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


def test_remote_translate_env_override(monkeypatch):
    cap = {}
    monkeypatch.setattr(rt, "OpenAI", lambda **kw: FakeClient(["x"], cap))
    monkeypatch.setenv("MODPACK_TRANSLATOR_REMOTE_MODEL", "env-model")
    cfg = ModelConfig(backend_mode="remote", remote_base_url="http://x/v1", remote_model="cfg-model")
    tr = rt.RemoteTranslator(cfg, "sys")
    tr.translate("hi")
    assert cap["model"] == "env-model"


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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_remote_translator.py -v`
Expected: FAIL（`ModuleNotFoundError: modpack_translator.pipeline.remote_translator`）。

- [ ] **Step 3: 實作 `remote_translator.py`**

Create `src/modpack_translator/pipeline/remote_translator.py`:
```python
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


class RemoteTranslator:
    """對遠端 OpenAI 相容 API 做串流翻譯。無本地 server 生命週期，close() 為 no-op。"""

    def __init__(self, cfg: ModelConfig, system_prompt: str) -> None:
        base_url = os.getenv("MODPACK_TRANSLATOR_REMOTE_URL") or cfg.remote_base_url
        api_key = os.getenv("MODPACK_TRANSLATOR_REMOTE_API_KEY") or cfg.remote_api_key
        model = os.getenv("MODPACK_TRANSLATOR_REMOTE_MODEL") or cfg.remote_model

        if not base_url:
            raise TranslatorFatalError("未設定遠端 API Base URL。")
        if not model:
            raise TranslatorFatalError("未設定遠端模型名稱。")

        self._cfg = cfg
        self._model = model
        self._system_prompt = system_prompt
        self._client = OpenAI(
            base_url=f"{normalize_base_url(base_url)}/v1",
            api_key=api_key or "not-needed",
        )

    def translate(self, text: str, cancel_check: Callable[[], bool] | None = None) -> str:
        return stream_chat(
            self._client,
            self._model,
            self._system_prompt,
            text,
            max_tokens=self._cfg.max_tokens,
            temperature=self._cfg.temperature,
            extra_body=None,  # 遠端只送標準 OpenAI 參數，不送 repeat_penalty
            cancel_check=cancel_check,
        )

    def close(self) -> None:
        return None

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
```

> 註：`pytest` 可能把名稱以 `test_` 開頭的模組層級函式當測試蒐集。`test_remote_connection` 定義在 `src/`（非 `tests/`），`testpaths=["tests"]` 已限制蒐集範圍，故不會被誤蒐集。

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_remote_translator.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/modpack_translator/pipeline/remote_translator.py tests/test_remote_translator.py
git commit -m "feat: 新增 RemoteTranslator 與 test_remote_connection"
```

---

## Task 4: 重構 `translator.py` 並加入 `build_translator` 工廠

**Files:**
- Modify: `src/modpack_translator/pipeline/translator.py`（第 151-153 行的 `_normalize_base_url`；第 402-425 行的 `translate`；檔尾新增工廠）
- Create: `tests/test_translator_factory.py`

**Interfaces:**
- Consumes: `modpack_translator.pipeline._chat`（`stream_chat`、`normalize_base_url`）；`modpack_translator.pipeline.remote_translator.RemoteTranslator`（工廠內延遲 import）。
- Produces: `def build_translator(cfg: ModelConfig, system_prompt: str)` → `backend_mode == "remote"` 回傳 `RemoteTranslator`，否則回傳 `GGUFTranslator`。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_translator_factory.py`:
```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_translator_factory.py -v`
Expected: FAIL（`build_translator` 不存在 → `AttributeError`）。

- [ ] **Step 3: 讓 `GGUFTranslator` 改用 `_chat`**

在 `src/modpack_translator/pipeline/translator.py` 頂部（`from modpack_translator.config import ModelConfig` 之後）加入：
```python
from modpack_translator.pipeline._chat import normalize_base_url, stream_chat
```

刪除第 151-153 行的本地定義：
```python
def _normalize_base_url(url: str) -> str:
    url = url.rstrip("/")
    return url[:-3] if url.endswith("/v1") else url
```

把第 287 行 `self._base_url = _normalize_base_url(server_url)` 改為：
```python
        self._base_url = normalize_base_url(server_url)
```

- [ ] **Step 4: 用 `stream_chat` 改寫 `GGUFTranslator.translate`**

把第 402-425 行的 `translate` 方法整段換成：
```python
    def translate(self, text: str, cancel_check: Callable[[], bool] | None = None) -> str:
        """翻譯單條字串，使用串流模式逐 token 生成。

        cancel_check 若回傳 True，立即中止並回傳空字串（使後處理驗證失敗，安全回退至原文）。
        """
        return stream_chat(
            self._client,
            self._model,
            self._system_prompt,
            text,
            max_tokens=self._cfg.max_tokens,
            temperature=self._cfg.temperature,
            extra_body={"repeat_penalty": self._cfg.repeat_penalty},
            cancel_check=cancel_check,
        )
```
（`Callable` 已於檔案頂部 `from typing import Callable` 匯入，無需改動。）

- [ ] **Step 5: 於檔尾新增工廠**

在 `src/modpack_translator/pipeline/translator.py` 檔尾新增：
```python
def build_translator(cfg: ModelConfig, system_prompt: str):
    """依 backend_mode 回傳對應的 translator。介面一致：translate() / close() / context manager。"""
    if cfg.backend_mode == "remote":
        from modpack_translator.pipeline.remote_translator import RemoteTranslator
        return RemoteTranslator(cfg, system_prompt)
    return GGUFTranslator(cfg, system_prompt)
```

- [ ] **Step 6: 執行測試確認通過**

Run: `uv run pytest tests/ -v`
Expected: PASS（全部；含既有 config／chat／remote 測試）。

- [ ] **Step 7: Commit**

```bash
git add src/modpack_translator/pipeline/translator.py tests/test_translator_factory.py
git commit -m "refactor: GGUFTranslator 改用 stream_chat，新增 build_translator 工廠"
```

---

## Task 5: 接線呼叫端（worker + CLI）與致命錯誤中止

**Files:**
- Modify: `src/modpack_translator/gui/worker.py`（第 18-26 行 import；第 137 行型別註解；第 166 行連線訊息；第 169 行建立 translator；第 195-211 行 per-target except）
- Modify: `scripts/translate_modpack.py`（第 27 行 import；第 169 行建立 translator；第 177-189 行 per-target except）
- Create: `tests/test_runner_fatal.py`

**Interfaces:**
- Consumes: `modpack_translator.pipeline.translator.build_translator`；`modpack_translator.pipeline._chat.TranslatorFatalError`。
- Produces: 無新公開 API；行為變更 —— `TranslatorFatalError` 不再被 per-target `except Exception` 吞掉，會往上冒出中止整趟。

- [ ] **Step 1: 寫失敗測試（runner 不吞致命錯誤）**

Create `tests/test_runner_fatal.py`:
```python
import pytest

import modpack_translator.pipeline.runner as runner
from modpack_translator.pipeline._chat import TranslatorFatalError


class BoomTranslator:
    def translate(self, text, cancel_check=None):
        raise TranslatorFatalError("金鑰錯誤")


def test_translate_dict_propagates_fatal(monkeypatch):
    # 強制該字串走「翻譯」路徑並讓驗證通過，確保會呼叫 translator.translate
    monkeypatch.setattr(runner, "classify_translation_entry", lambda k, s: "translate")
    monkeypatch.setattr(runner, "is_usable_translation", lambda s, t: True)
    with pytest.raises(TranslatorFatalError):
        runner.translate_dict({"k": "Hello world"}, {}, BoomTranslator(), {}, retry_count=0)
```

- [ ] **Step 2: 執行測試確認通過（驗證 runner 本來就不吞）**

Run: `uv run pytest tests/test_runner_fatal.py -v`
Expected: PASS。此測試確立 `runner.translate_dict` 會讓 `TranslatorFatalError` 往上傳遞（呼叫端才是需要修改的地方）。若此步意外 FAIL，表示 runner 內有非預期的攔截，需先排除再繼續。

- [ ] **Step 3: 修改 `worker.py` import 與型別註解**

`src/modpack_translator/gui/worker.py` 第 26 行：
```python
from modpack_translator.pipeline.translator import GGUFTranslator
```
改為：
```python
from modpack_translator.pipeline._chat import TranslatorFatalError
from modpack_translator.pipeline.translator import build_translator
```

第 137 行：
```python
        self._translator: GGUFTranslator | None = None
```
改為：
```python
        self._translator = None
```

- [ ] **Step 4: 修改 `worker.py` 連線訊息與建立 translator**

第 166-169 行：
```python
            self.log.emit("正在連線或啟動本機模型服務，請稍候…")
            translator = None
            try:
                translator = GGUFTranslator(self._cfg.model, self._cfg.language.system_prompt)
```
改為：
```python
            if self._cfg.model.backend_mode == "remote":
                self.log.emit("正在連線遠端 API，請稍候…")
            else:
                self.log.emit("正在連線或啟動本機模型服務，請稍候…")
            translator = None
            try:
                translator = build_translator(self._cfg.model, self._cfg.language.system_prompt)
```

- [ ] **Step 5: 修改 `worker.py` per-target except，讓致命錯誤中止**

第 195-211 行的 per-target `try/except`（`n_t, n_c, n_f, failed = process_target(...)` 那段）中，於 `except Exception as exc:` 之前加入一個更專一的 except：
```python
                    except TranslatorFatalError:
                        raise
                    except Exception as exc:
                        self.log.emit(f"[警告] 略過 {target.mod_id}/{target.format}：{exc}")
                        continue
```
（`TranslatorFatalError` 會冒到 `run()` 最外層 `except Exception as exc: self.error.emit(str(exc))`，彈出錯誤對話框並中止整趟；`finally` 仍會 `translator.close()`。）

- [ ] **Step 6: 修改 `scripts/translate_modpack.py`**

第 27 行：
```python
from modpack_translator.pipeline.translator import GGUFTranslator
```
改為：
```python
from modpack_translator.pipeline._chat import TranslatorFatalError
from modpack_translator.pipeline.translator import build_translator
```

第 168-169 行：
```python
    print("\n正在連線或啟動本機模型服務…")
    translator = GGUFTranslator(cfg.model, cfg.language.system_prompt)
```
改為：
```python
    if cfg.model.backend_mode == "remote":
        print("\n正在連線遠端 API…")
    else:
        print("\n正在連線或啟動本機模型服務…")
    translator = build_translator(cfg.model, cfg.language.system_prompt)
```

第 177-189 行 per-target `try/except`，於 `except Exception as exc:` 之前加入：
```python
            except TranslatorFatalError:
                raise
            except Exception as exc:
                tqdm.write(f"[警告] 略過 {target.mod_id}/{target.format}：{exc}")
                continue
```

- [ ] **Step 7: 執行全部測試 + import 檢查**

Run:
```bash
uv run pytest tests/ -v
uv run python -c "import modpack_translator.gui.worker; import scripts.translate_modpack; print('import OK')"
```
Expected: pytest 全數 PASS；import 印出 `import OK`（確認改動未破壞模組匯入）。

- [ ] **Step 8: Commit**

```bash
git add src/modpack_translator/gui/worker.py scripts/translate_modpack.py tests/test_runner_fatal.py
git commit -m "feat: worker/CLI 改用工廠，致命錯誤中止整趟翻譯"
```

---

## Task 6: GUI 後端模式切換與遠端面板

**Files:**
- Modify: `src/modpack_translator/gui/main_window.py`

**Interfaces:**
- Consumes: `modpack_translator.pipeline.remote_translator.test_remote_connection`（於 `ConnTestWorker` 內延遲 import）。
- Produces: GUI 行為 —— 使用者可在「模型設定」切換本地／遠端；遠端欄位（Base URL／API Key／模型名）；測試連線按鈕；設定存 QSettings；`_build_cfg` 注入 `cfg.model.backend_mode` 與 `remote_*`；遠端模式時 `_start_translation` 跳過 LoRA 檢查。

> 本任務為 GUI 版面與訊號接線，採**手動驗證**（專案無 GUI 測試基礎；核心遠端邏輯已於 Task 2-3 單元測試）。

- [ ] **Step 1: 補匯入 QRadioButton / QButtonGroup**

`src/modpack_translator/gui/main_window.py` 的 `from PySide6.QtWidgets import (...)`（第 9-26 行）清單中加入 `QButtonGroup,` 與 `QRadioButton,`（維持字母順序附近即可）。

- [ ] **Step 2: 於 `__init__` 新增成員並在建好 UI 後載入遠端設定**

在 `__init__` 中，`self._update_check_worker = None`（第 78 行附近）之後加入：
```python
        self._conn_test_worker = None
```
在 `__init__` 尾端 `self._update_theme_button()`（第 118 行）之後、`QTimer.singleShot(...)`（第 119 行）之前加入：
```python
        self._load_remote_settings()
```

- [ ] **Step 3: 改寫「模型設定」群組（第 182-240 行）**

把第 182-240 行（`model_group = QGroupBox("模型設定")` 起，到 `root_layout.addWidget(model_group)` 止）整段替換為：
```python
        # ── 模型設定群組 ──────────────────────────────────────────────────
        model_group = QGroupBox("模型設定")
        model_vbox = QVBoxLayout(model_group)

        # 後端模式切換
        mode_row = QHBoxLayout()
        self.backend_local_radio = QRadioButton("本地模型")
        self.backend_remote_radio = QRadioButton("遠端 API")
        self.backend_local_radio.setChecked(True)
        self._backend_group = QButtonGroup(self)
        self._backend_group.addButton(self.backend_local_radio)
        self._backend_group.addButton(self.backend_remote_radio)
        self.backend_local_radio.toggled.connect(self._on_backend_mode_changed)
        mode_help = _make_help_label(
            "本地模型：使用本機 llama.cpp server（需先執行初始化腳本）。\n"
            "遠端 API：使用 OpenAI 相容的遠端端點（OpenAI / OpenRouter / Groq / 自架 vLLM…）。"
        )
        mode_row.addWidget(QLabel("後端模式："))
        mode_row.addWidget(self.backend_local_radio)
        mode_row.addWidget(self.backend_remote_radio)
        mode_row.addWidget(mode_help)
        mode_row.addStretch()
        model_vbox.addLayout(mode_row)

        # ── 本地模型欄位（容器，遠端模式時整塊隱藏）───────────────────────
        self.local_box = QWidget()
        mgf = QFormLayout(self.local_box)
        mgf.setContentsMargins(0, 0, 0, 0)
        mgf.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        lora_row = QHBoxLayout()
        self.lora_edit = QLineEdit()
        self.lora_edit.setText(
            self._cfg.model.lora_gguf_path if self._cfg else "adapter/minecraft_translator_gemma4_e4b_lora.gguf"
        )
        _browse_lora_btn = QPushButton("瀏覽…")
        _browse_lora_btn.setFixedWidth(80)
        _browse_lora_btn.clicked.connect(self._browse_gguf)
        lora_help = _make_help_label(
            "LoRA 適配器為微調後的模型差異檔（.gguf），提供 Minecraft 翻譯專用能力。\n"
            "必須與基礎模型搭配使用。"
        )
        lora_row.addWidget(self.lora_edit)
        lora_row.addWidget(_browse_lora_btn)
        lora_row.addWidget(lora_help)
        mgf.addRow("LoRA 適配器：", lora_row)

        base_row = QHBoxLayout()
        self.base_gguf_edit = QLineEdit()
        self.base_gguf_edit.setPlaceholderText("留空自動下載（約 5 GB，僅首次）")
        self.base_gguf_edit.setText(self._cfg.model.base_gguf_path if self._cfg else "")
        _browse_base_btn = QPushButton("瀏覽…")
        _browse_base_btn.setFixedWidth(80)
        _browse_base_btn.clicked.connect(self._browse_base_gguf)
        base_help = _make_help_label(
            "基礎模型 GGUF 檔（約 5 GB）。\n"
            "留空時程式自動從 HuggingFace 下載並快取，僅首次需要網路連線。"
        )
        base_row.addWidget(self.base_gguf_edit)
        base_row.addWidget(_browse_base_btn)
        base_row.addWidget(base_help)
        mgf.addRow("基礎模型：", base_row)

        gpu_row = QHBoxLayout()
        self.gpu_layers_spin = QSpinBox()
        self.gpu_layers_spin.setRange(-1, 200)
        self.gpu_layers_spin.setValue(self._cfg.model.n_gpu_layers if self._cfg else -1)
        self.gpu_layers_spin.setFixedWidth(70)
        gpu_help = _make_help_label(
            "指定卸載至 GPU 的模型層數。\n"
            "-1 = 全部卸載至 GPU（最快）\n"
            " 0 = 僅使用 CPU（最慢但相容性最高）\n"
            "修改後請重新執行初始化腳本，讓本機模型服務設定生效。"
        )
        gpu_row.addWidget(self.gpu_layers_spin)
        gpu_row.addWidget(QLabel("  （−1 = 全 GPU，0 = 僅 CPU）"))
        gpu_row.addWidget(gpu_help)
        gpu_row.addStretch()
        mgf.addRow("GPU 層數：", gpu_row)

        model_vbox.addWidget(self.local_box)

        # ── 遠端 API 欄位（容器，本地模式時整塊隱藏）─────────────────────
        self.remote_box = QWidget()
        rgf = QFormLayout(self.remote_box)
        rgf.setContentsMargins(0, 0, 0, 0)
        rgf.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.remote_url_edit = QLineEdit()
        self.remote_url_edit.setPlaceholderText("https://api.openai.com/v1")
        self.remote_url_edit.textChanged.connect(self._save_remote_settings)
        url_help = _make_help_label("遠端 OpenAI 相容端點的 Base URL，通常以 /v1 結尾。")
        url_row = QHBoxLayout()
        url_row.addWidget(self.remote_url_edit)
        url_row.addWidget(url_help)
        rgf.addRow("Base URL：", url_row)

        self.remote_key_edit = QLineEdit()
        self.remote_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remote_key_edit.setPlaceholderText("sk-...")
        self.remote_key_edit.textChanged.connect(self._save_remote_settings)
        self.remote_key_show_btn = QPushButton("👁")
        self.remote_key_show_btn.setCheckable(True)
        self.remote_key_show_btn.setFixedWidth(36)
        self.remote_key_show_btn.setToolTip("顯示 / 隱藏金鑰")
        self.remote_key_show_btn.toggled.connect(self._toggle_key_visibility)
        key_help = _make_help_label("API 金鑰，儲存在本機 QSettings（明文）。自架且不需金鑰時可留空。")
        key_row = QHBoxLayout()
        key_row.addWidget(self.remote_key_edit)
        key_row.addWidget(self.remote_key_show_btn)
        key_row.addWidget(key_help)
        rgf.addRow("API Key：", key_row)

        self.remote_model_edit = QLineEdit()
        self.remote_model_edit.setPlaceholderText("例如 gpt-4o-mini")
        self.remote_model_edit.textChanged.connect(self._save_remote_settings)
        model_help = _make_help_label("遠端模型名稱，需與該端點提供的模型一致。")
        rmodel_row = QHBoxLayout()
        rmodel_row.addWidget(self.remote_model_edit)
        rmodel_row.addWidget(model_help)
        rgf.addRow("模型名稱：", rmodel_row)

        test_row = QHBoxLayout()
        self.test_conn_btn = QPushButton("測試連線")
        self.test_conn_btn.setFixedWidth(96)
        self.test_conn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_conn_btn.clicked.connect(self._on_test_connection)
        self.test_conn_label = QLabel("")
        self.test_conn_label.setObjectName("statsLabel")
        test_help = _make_help_label("以極短請求測試 URL／金鑰／模型名是否正確（約消耗 1 個 token）。")
        test_row.addWidget(self.test_conn_btn)
        test_row.addWidget(self.test_conn_label)
        test_row.addWidget(test_help)
        test_row.addStretch()
        rgf.addRow("", test_row)

        model_vbox.addWidget(self.remote_box)
        self.remote_box.setVisible(False)

        root_layout.addWidget(model_group)
```

- [ ] **Step 4: 新增模式切換、QSettings、金鑰顯示、測試連線的方法**

在 `MainWindow` 內（例如 `_browse_base_gguf` 方法之後）新增：
```python
    # ------------------------------------------------------------------ 後端模式 / 遠端設定

    def _on_backend_mode_changed(self, *_):
        remote = self.backend_remote_radio.isChecked()
        self.local_box.setVisible(not remote)
        self.remote_box.setVisible(remote)
        self._save_remote_settings()

    def _toggle_key_visibility(self, checked: bool):
        self.remote_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _load_remote_settings(self):
        self.remote_url_edit.setText(self._settings.value("model/remote_base_url", "") or "")
        self.remote_key_edit.setText(self._settings.value("model/remote_api_key", "") or "")
        self.remote_model_edit.setText(self._settings.value("model/remote_model", "") or "")
        mode = self._settings.value("model/backend_mode", "local") or "local"
        if mode == "remote":
            self.backend_remote_radio.setChecked(True)
        else:
            self.backend_local_radio.setChecked(True)
        self._on_backend_mode_changed()

    def _save_remote_settings(self, *_):
        mode = "remote" if self.backend_remote_radio.isChecked() else "local"
        self._settings.setValue("model/backend_mode", mode)
        self._settings.setValue("model/remote_base_url", self.remote_url_edit.text().strip())
        self._settings.setValue("model/remote_api_key", self.remote_key_edit.text().strip())
        self._settings.setValue("model/remote_model", self.remote_model_edit.text().strip())

    def _on_test_connection(self):
        base = self.remote_url_edit.text().strip()
        model = self.remote_model_edit.text().strip()
        key = self.remote_key_edit.text().strip()
        if not base or not model:
            self.test_conn_label.setText("✗ 請先填寫 Base URL 與模型名稱")
            return
        self.test_conn_btn.setEnabled(False)
        self.test_conn_label.setText("測試中…")
        self._conn_test_worker = ConnTestWorker(base, key, model)
        self._conn_test_worker.done.connect(self._on_test_connection_done)
        self._conn_test_worker.start()

    def _on_test_connection_done(self, ok: bool, msg: str):
        self.test_conn_btn.setEnabled(True)
        self.test_conn_label.setText(("✓ " if ok else "✗ ") + msg)
```

- [ ] **Step 5: `_build_cfg` 注入遠端設定**

在 `_build_cfg`（第 461-476 行）的 `cfg.paths.create_output_dirs()` 之前，插入：
```python
        if self.backend_remote_radio.isChecked():
            cfg.model.backend_mode = "remote"
            cfg.model.remote_base_url = self.remote_url_edit.text().strip()
            cfg.model.remote_api_key = self.remote_key_edit.text().strip()
            cfg.model.remote_model = self.remote_model_edit.text().strip()
        else:
            cfg.model.backend_mode = "local"
```

- [ ] **Step 6: `_start_translation` 遠端模式跳過 LoRA 檢查**

`_start_translation`（第 620-635 行）中，把這段：
```python
        lora_path = Path(cfg.model.lora_gguf_path)
        if not lora_path.is_absolute():
            lora_path = _PROJECT_ROOT / lora_path
        if not lora_path.exists():
            QMessageBox.warning(self, "找不到 LoRA 適配器",
                                f"找不到 LoRA 適配器 GGUF：\n{lora_path}")
            return
```
替換為：
```python
        if cfg.model.backend_mode == "remote":
            if not cfg.model.remote_base_url or not cfg.model.remote_model:
                QMessageBox.warning(self, "遠端設定不完整",
                                    "請先填寫遠端 API 的 Base URL 與模型名稱。")
                return
        else:
            lora_path = Path(cfg.model.lora_gguf_path)
            if not lora_path.is_absolute():
                lora_path = _PROJECT_ROOT / lora_path
            if not lora_path.exists():
                QMessageBox.warning(self, "找不到 LoRA 適配器",
                                    f"找不到 LoRA 適配器 GGUF：\n{lora_path}")
                return
```

- [ ] **Step 7: 新增 `ConnTestWorker` 類別**

在檔尾（`UpdateDownloadWorker` 類別之後）新增：
```python
class ConnTestWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, base_url: str, api_key: str, model: str):
        super().__init__()
        self._base_url = base_url
        self._api_key = api_key
        self._model = model

    def run(self):
        from modpack_translator.pipeline.remote_translator import test_remote_connection
        ok, msg = test_remote_connection(self._base_url, self._api_key, self._model)
        self.done.emit(ok, msg)
```

- [ ] **Step 8: 匯入健全性檢查**

Run:
```bash
uv run python -c "import modpack_translator.gui.main_window; print('import OK')"
uv run pytest tests/ -v
```
Expected: 印出 `import OK`；pytest 全數 PASS（GUI 改動不應影響既有測試）。

- [ ] **Step 9: 手動驗證**

啟動：`uv run python main.py`，逐項確認：
1. 「模型設定」頂端出現「後端模式：(•)本地模型 ( )遠端 API」，預設為本地，顯示 LoRA／基礎模型／GPU 三欄。
2. 點「遠端 API」→ 本地三欄隱藏，出現 Base URL／API Key／模型名稱／測試連線。
3. API Key 欄預設為密碼點點；按「👁」可切換明碼。
4. 填入任意 Base URL 與模型名稱，按「測試連線」→ 按鈕短暫變灰、顯示「測試中…」，隨後顯示 ✓/✗ 結果（無真實端點時應為 ✗，訊息合理）。
5. 關閉並重開程式 → 上次選的模式與填寫的欄位都還在（QSettings 已保存）。
6. 切回「本地模型」→ 遠端欄位隱藏、本地三欄回來。

- [ ] **Step 10: Commit**

```bash
git add src/modpack_translator/gui/main_window.py
git commit -m "feat: GUI 新增本地/遠端切換與遠端 API 面板（含測試連線）"
```

---

## Task 7: 文件更新

**Files:**
- Modify: `README_zh.md`
- Modify: `README.md`

**Interfaces:** 無程式介面。

- [ ] **Step 1: 更新 `README_zh.md`**

在說明模型／使用方式的段落，新增一節「使用遠端 OpenAI 相容 API」，內容涵蓋：
```markdown
### 使用遠端 OpenAI 相容 API（可選）

除了本機模型，也可改用遠端 OpenAI 相容端點（OpenAI、OpenRouter、Groq、自架 vLLM 等）。

**GUI：** 在「模型設定 → 後端模式」選「遠端 API」，填入 Base URL（例如 `https://api.openai.com/v1`）、
API Key 與模型名稱（例如 `gpt-4o-mini`），可按「測試連線」確認設定正確。設定會保存在本機。

**CLI／進階：** 於 `configs/model.yaml` 設定 `backend_mode: "remote"` 與 `remote_base_url` /
`remote_api_key` / `remote_model`；或用環境變數 `MODPACK_TRANSLATOR_REMOTE_URL` /
`MODPACK_TRANSLATOR_REMOTE_API_KEY` / `MODPACK_TRANSLATOR_REMOTE_MODEL` 覆寫。

注意：遠端模式按供應商計費（模組包字串眾多），但有翻譯快取，重跑僅計費新字串。
```

- [ ] **Step 2: 更新 `README.md`（英文對應段落）**

新增對應英文段落：
```markdown
### Using a remote OpenAI-compatible API (optional)

Besides the local model, you can point the translator at any remote OpenAI-compatible
endpoint (OpenAI, OpenRouter, Groq, self-hosted vLLM, etc.).

**GUI:** In "Model settings → Backend mode", choose "Remote API", then fill in the Base URL
(e.g. `https://api.openai.com/v1`), API Key, and model name (e.g. `gpt-4o-mini`). Use
"Test connection" to verify. Settings are saved locally.

**CLI / advanced:** Set `backend_mode: "remote"` plus `remote_base_url` / `remote_api_key` /
`remote_model` in `configs/model.yaml`, or override with the environment variables
`MODPACK_TRANSLATOR_REMOTE_URL` / `MODPACK_TRANSLATOR_REMOTE_API_KEY` / `MODPACK_TRANSLATOR_REMOTE_MODEL`.

Note: remote mode is billed per provider (modpacks contain many strings), but the translation
cache means re-runs only pay for new strings.
```

- [ ] **Step 3: Commit**

```bash
git add README.md README_zh.md
git commit -m "docs: 補充遠端 OpenAI 相容 API 使用說明"
```

---

## Self-Review 對照

- **Spec 覆蓋**：`backend_mode`/`remote_*` 欄位（Task 1）；`stream_chat` + `repeat_penalty` 修正 + `normalize_base_url`（Task 2、4）；`RemoteTranslator` + `test_remote_connection`（Task 3）；工廠（Task 4）；worker/CLI 接線 + 致命錯誤中止（Task 5）；GUI 面板 + 測試連線 + 跳過 LoRA 檢查 + QSettings（Task 6）；測試策略（Task 1-5 皆含 mock 測試）；文件（Task 7）。皆有對應。
- **實機驗證限制**：對真實遠端端點的端對端翻譯需使用者提供 API 金鑰，計畫內僅 mock；此為 spec 第 7 節已載明的已知限制。
- **型別一致性**：`build_translator(cfg, system_prompt)`、`RemoteTranslator(cfg, system_prompt)`、`test_remote_connection(base_url, api_key, model, timeout=15.0)`、`stream_chat(client, model, system_prompt, text, max_tokens, temperature, extra_body=None, cancel_check=None)`、`ConnTestWorker(base_url, api_key, model)`、`TranslatorFatalError` 於各 Task 使用一致。
- **無 placeholder**：各步驟均含實際程式碼與指令。
```
