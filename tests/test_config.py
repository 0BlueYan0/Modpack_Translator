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
