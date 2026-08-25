import os
from pathlib import Path
import pytest
from app.config import AgentConfig, RootConfig, load_config, resolve_env_vars

# conf.toml is gitignored; fall back to the committed template on a fresh clone.
CONFIG_PATH = "conf.toml" if Path("conf.toml").exists() else "conf.example.toml"


def test_resolve_env_vars(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret_value_123")
    data = {
        "key1": "${TEST_KEY}",
        "key2": "${NON_EXISTENT_VAR:-default_val}",
        "nested": {"list": ["${TEST_KEY}"]}
    }
    resolved = resolve_env_vars(data)
    assert resolved["key1"] == "secret_value_123"
    assert resolved["key2"] == "default_val"
    assert resolved["nested"]["list"] == ["secret_value_123"]


def test_load_conf_toml():
    cfg = load_config(CONFIG_PATH)
    assert cfg.app.host == "127.0.0.1"
    assert cfg.app.port == 8000
    assert "orchestrator" in cfg.agents
    assert "architect" in cfg.agents
    assert "coder" in cfg.agents
    assert "critic" in cfg.agents
    assert cfg.agents["orchestrator"].role == "Moderator & Synthesizer"


def test_orchestrator_required():
    invalid_data = {
        "app": {"host": "127.0.0.1", "port": 8000},
        "agents": {
            "coder": {"name": "Coder", "role": "Dev", "model": "gpt-4o"}
        }
    }
    with pytest.raises(ValueError, match="orchestrator"):
        RootConfig.model_validate(invalid_data)
