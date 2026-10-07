"""All runtime settings in one validated place, read from the environment.

Entry points (server, chat, evals, improve) call `load_env()` once; everything else asks
`get_settings()`. Tests build a `Settings(...)` directly and pass it in.
Also defines the schema for config/agent.yaml, so a bad config (hand-edited or written by the
improvement loop) fails at load time with a clear message instead of mid-conversation.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    # Files
    config_path: Path = ROOT / "config" / "agent.yaml"
    clinic_data_path: Path = ROOT / "data" / "clinic.json"
    # Model calls
    agent_model: str | None = None  # overrides config.model when set
    llm_timeout_s: float = Field(20.0, gt=0)  # per LLM request
    llm_max_retries: int = Field(2, ge=0)
    turn_deadline_s: float = Field(60.0, gt=0)  # whole patient turn, all tool steps included
    # Conversation limits
    max_turns: int = Field(40, ge=1)
    max_message_chars: int = Field(2000, ge=1)
    # Web server
    host: str = "127.0.0.1"
    port: int = 8000
    max_sessions: int = Field(200, ge=1)
    session_idle_ttl_s: float = Field(1800.0, gt=0)
    demo_mode: bool = False  # exposes internals (tool calls, patient list) in the UI
    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        values = {
            "config_path": env.get("YOURHEALTH_CONFIG"),
            "clinic_data_path": env.get("YOURHEALTH_CLINIC_DATA"),
            "agent_model": env.get("AGENT_MODEL"),
            "llm_timeout_s": env.get("LLM_TIMEOUT_S"),
            "llm_max_retries": env.get("LLM_MAX_RETRIES"),
            "turn_deadline_s": env.get("TURN_DEADLINE_S"),
            "max_turns": env.get("MAX_TURNS"),
            "host": env.get("HOST"),
            "port": env.get("PORT"),
            "max_sessions": env.get("MAX_SESSIONS"),
            "session_idle_ttl_s": env.get("SESSION_IDLE_TTL_S"),
            "log_level": env.get("LOG_LEVEL"),
        }
        values = {k: v for k, v in values.items() if v not in (None, "")}
        values["demo_mode"] = _env_bool("DEMO_MODE", False)
        return cls(**values)


def load_env() -> None:
    """Load .env from the project root (no-op if absent). Call once from an entry point."""
    load_dotenv(ROOT / ".env")
    get_settings.cache_clear()


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


# ---- agent config schema (config/agent.yaml) ------------------------------------

class LearnedRule(BaseModel):
    id: str
    rule: str = Field(min_length=1)
    why: str = ""
    fixes: list[str] = []
    learned_from: str = ""


class AgentConfig(BaseModel):
    version: int = Field(ge=1)
    model: str
    temperature: float = Field(ge=0, le=2)
    max_tool_steps: int = Field(ge=1, le=20)
    core_prompt: str
    learned_rules: list[LearnedRule] = []

    @field_validator("core_prompt")
    @classmethod
    def _has_placeholders(cls, v: str) -> str:
        missing = [p for p in ("{clinic_name}", "{now}", "{calendar}") if p not in v]
        if missing:
            raise ValueError(f"core_prompt is missing placeholders {missing}")
        return v


class ConfigError(ValueError):
    pass


def validate_config(raw: dict) -> dict:
    """Validate and normalise an agent config. Returns a plain dict (the rest of the code uses dicts)."""
    try:
        return AgentConfig.model_validate(raw).model_dump()
    except ValidationError as e:
        raise ConfigError(f"invalid agent config: {e}") from e
