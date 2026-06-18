"""Tests for the per-model config resolution (config.model_context_window,
_role_model, _voice_setting) and the models.toml ← models.example.toml merge.

All model config (roles + context windows + voice) lives in models.toml, merged
on top of models.example.toml ; an env var always wins. Resolution is capped at
the ceiling so no single model's KV cache can blow VRAM.
"""

from __future__ import annotations

import pytest

from jeanmichel import config

# ---- model_context_window -------------------------------------------------


def test_ctx_env_wins_and_is_capped(monkeypatch):
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"context_window": {"foo:bar": 9999}})
    # env beats the table…
    monkeypatch.setenv("JEANMICHEL_CTX_WINDOW_foo_bar", "20000")
    assert config.model_context_window("foo:bar") == 20000
    # …but is still capped at the ceiling.
    monkeypatch.setenv("JEANMICHEL_CTX_WINDOW_foo_bar", "256000")
    assert config.model_context_window("foo:bar") == config.MODEL_CONTEXT_CEILING


def test_ctx_from_table(monkeypatch):
    monkeypatch.delenv("JEANMICHEL_CTX_WINDOW_foo_bar", raising=False)
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"context_window": {"foo:bar": 40960}})
    assert config.model_context_window("foo:bar") == 40960


def test_ctx_default_for_unknown(monkeypatch):
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"default": 12345, "context_window": {}})
    assert config.model_context_window("never-heard-of:it") == 12345


def test_ctx_default_falls_back_to_constant(monkeypatch):
    monkeypatch.setattr(config, "_MODELS_CONFIG", {})
    assert config.model_context_window("x:y") == config.DEFAULT_MODEL_CONTEXT_WINDOW


def test_ctx_table_value_capped(monkeypatch):
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"context_window": {"big:one": 300000}})
    assert config.model_context_window("big:one") == config.MODEL_CONTEXT_CEILING


def test_ctx_custom_ceiling_from_config(monkeypatch):
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"ceiling": 16000, "context_window": {"m:1": 50000}})
    assert config.model_context_window("m:1") == 16000


# ---- _role_model ----------------------------------------------------------


def test_role_env_wins(monkeypatch):
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"roles": {"main": "from-toml"}})
    monkeypatch.setenv("JEANMICHEL_MAIN_MODEL", "from-env")
    assert config._role_model("JEANMICHEL_MAIN_MODEL", "main") == "from-env"


def test_role_from_toml(monkeypatch):
    monkeypatch.delenv("JEANMICHEL_MAIN_MODEL", raising=False)
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"roles": {"main": "from-toml"}})
    assert config._role_model("JEANMICHEL_MAIN_MODEL", "main") == "from-toml"


def test_role_unresolved_raises(monkeypatch):
    """No hardcoded default : if neither env nor toml provides the role, fail loudly."""
    monkeypatch.delenv("JEANMICHEL_MAIN_MODEL", raising=False)
    monkeypatch.setattr(config, "_MODELS_CONFIG", {})
    with pytest.raises(RuntimeError):
        config._role_model("JEANMICHEL_MAIN_MODEL", "main")


# ---- agent_model (env per-agent → DB override → role default) -------------


def test_agent_model_env_per_agent_wins(monkeypatch):
    monkeypatch.setenv("JEANMICHEL_AGENT_MODEL_CODE_RUNNER", "from-env")
    assert config.agent_model("code-runner", "specialist", "db-override") == "from-env"


def test_agent_model_db_override(monkeypatch):
    monkeypatch.delenv("JEANMICHEL_AGENT_MODEL_CODE_RUNNER", raising=False)
    assert config.agent_model("code-runner", "specialist", "qwen3-coder:latest") == "qwen3-coder:latest"


def test_agent_model_role_default(monkeypatch):
    monkeypatch.delenv("JEANMICHEL_AGENT_MODEL_WEB_SEARCH_SPECIALIST", raising=False)
    monkeypatch.delenv("JEANMICHEL_AGENT_MODEL_JEAN_MICHEL", raising=False)
    monkeypatch.setattr(config, "MAIN_MODEL", "main-m")
    monkeypatch.setattr(config, "SUBAGENT_DEFAULT_MODEL", "sub-m")
    assert config.agent_model("web-search-specialist", "specialist", None) == "sub-m"
    assert config.agent_model("jean-michel", "router", None) == "main-m"


# ---- _voice_setting -------------------------------------------------------


def test_voice_env_then_toml_then_default(monkeypatch):
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"voice": {"model": "/toml/v.onnx"}})
    monkeypatch.setenv("JEANMICHEL_VOICE_MODEL", "/env/v.onnx")
    assert config._voice_setting("JEANMICHEL_VOICE_MODEL", "model", "/def") == "/env/v.onnx"
    monkeypatch.delenv("JEANMICHEL_VOICE_MODEL", raising=False)
    assert config._voice_setting("JEANMICHEL_VOICE_MODEL", "model", "/def") == "/toml/v.onnx"
    monkeypatch.setattr(config, "_MODELS_CONFIG", {})
    assert config._voice_setting("JEANMICHEL_VOICE_MODEL", "model", "/def") == "/def"


# ---- merge (example base + local override) --------------------------------


def test_load_merges_local_over_example(monkeypatch, tmp_path):
    example = tmp_path / "models.example.toml"
    local = tmp_path / "models.toml"
    example.write_text(
        '[roles]\nmain = "gemma"\ncode = "qwen"\n[context_window]\n"a:1" = 1000\n',
        encoding="utf-8",
    )
    local.write_text('[roles]\nmain = "nemotron"\n', encoding="utf-8")  # partial override
    monkeypatch.setattr(config, "MODELS_EXAMPLE_PATH", example)
    monkeypatch.setattr(config, "MODELS_CONFIG_PATH", local)
    merged = config._load_models_config()
    assert merged["roles"]["main"] == "nemotron"   # overridden
    assert merged["roles"]["code"] == "qwen"        # kept from example
    assert merged["context_window"]["a:1"] == 1000  # section untouched by partial override


def test_load_without_local_uses_example(monkeypatch, tmp_path):
    example = tmp_path / "models.example.toml"
    example.write_text('[roles]\nmain = "gemma"\n', encoding="utf-8")
    monkeypatch.setattr(config, "MODELS_EXAMPLE_PATH", example)
    monkeypatch.setattr(config, "MODELS_CONFIG_PATH", tmp_path / "absent.toml")
    assert config._load_models_config()["roles"]["main"] == "gemma"


# ---- shipped defaults sanity (the real example file) ----------------------


def test_real_example_has_sane_defaults():
    cfg = config._read_toml(config.MODELS_EXAMPLE_PATH)
    assert cfg["context_window"]["qwen3-coder:latest"] == 128000  # code keeps its big window
    assert cfg["context_window"]["granite4.1:8b"] <= 16000        # dispatcher stays small
    assert cfg["roles"]["dispatch"] and cfg["roles"]["main"] and cfg["roles"]["code"]
    assert "cogito:32b" in cfg["no_thinking"]                     # orchestrator has no think channel


# ---- model_skips_thinking -------------------------------------------------


def test_model_skips_thinking(monkeypatch):
    monkeypatch.setattr(config, "_MODELS_CONFIG", {"no_thinking": ["cogito:32b"]})
    assert config.model_skips_thinking("cogito:32b") is True
    assert config.model_skips_thinking("gemma4:26b") is False
    monkeypatch.setattr(config, "_MODELS_CONFIG", {})  # no list → nobody skips
    assert config.model_skips_thinking("cogito:32b") is False
