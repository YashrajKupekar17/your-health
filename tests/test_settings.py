import copy

import pytest

from yourhealth.agent import load_config
from yourhealth.settings import ConfigError, Settings, validate_config


def test_shipped_config_is_valid():
    cfg = load_config()
    assert cfg["version"] >= 1 and "{calendar}" in cfg["core_prompt"]


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda c: c.pop("model"), "model"),
        (lambda c: c.update(temperature=5), "temperature"),
        (lambda c: c.update(core_prompt="no placeholders"), "placeholders"),
        (lambda c: c.update(learned_rules=[{"id": "R1", "rule": ""}]), "rule"),
    ],
)
def test_bad_config_fails_fast(mutate, msg):
    cfg = copy.deepcopy(load_config())
    mutate(cfg)
    with pytest.raises(ConfigError, match=msg):
        validate_config(cfg)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_S", "7.5")
    monkeypatch.setenv("MAX_TURNS", "5")
    monkeypatch.setenv("DEMO_MODE", "true")
    s = Settings.from_env()
    assert (s.llm_timeout_s, s.max_turns, s.demo_mode) == (7.5, 5, True)


def test_settings_reject_nonsense(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_S", "-1")
    with pytest.raises(ValueError):
        Settings.from_env()
