import pytest
from pydantic import ValidationError

from modpack_translator.config import ModelConfig


def test_model_config_remote_defaults():
    c = ModelConfig()
    assert c.backend_mode == "local"
    assert c.remote_base_url == ""
    assert c.remote_api_key == ""
    assert c.remote_model == ""


def test_model_config_prefill_defaults():
    c = ModelConfig()
    assert c.remote_prefill is True
    assert c.remote_batch_size == 12
    assert c.remote_concurrency == 6
    assert c.remote_timeout_s == 120.0
    assert c.remote_backoff_retries == 4


def test_model_config_prefill_bounds():
    with pytest.raises(ValidationError):
        ModelConfig(remote_batch_size=0)
    with pytest.raises(ValidationError):
        ModelConfig(remote_batch_size=65)
    with pytest.raises(ValidationError):
        ModelConfig(remote_concurrency=0)
    with pytest.raises(ValidationError):
        ModelConfig(remote_concurrency=33)
    with pytest.raises(ValidationError):
        ModelConfig(remote_timeout_s=0)
    with pytest.raises(ValidationError):
        ModelConfig(remote_backoff_retries=-1)


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
