"""The agent: one LLM in a tool-calling loop, wrapped by code it cannot bypass.

Per patient message:
  1. turn += 1 (this is what lets confirm_pending tell a later turn from the same turn)
  2. emergency gate (code) -> fixed reply + urgent handoff, LLM never runs
  3. LLM <-> tools loop, capped at max_tool_steps
  4. if the step cap, the turn deadline or the conversation turn limit is hit, or the API fails,
     hand off rather than leave the patient stuck. Every LLM request also has its own timeout.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from pathlib import Path

import yaml
from openai import OpenAI

from .clinic import Clinic
from .logs import get_logger, log_event
from .safety import EMERGENCY_REPLY, emergency_match
from .settings import Settings, get_settings, validate_config
from .tools import TOOL_SCHEMAS, Session, dispatch

FALLBACK_REPLY = "Sorry, I'm having trouble with that. Let me connect you with our front desk."
ENDED_REPLY = "A member of our staff will take it from here."
log = get_logger("agent")
TOO_LONG_REPLY = "We've covered a lot. Let me connect you with our front desk so they can finish this with you."
GREETING = "Hi, this is {clinic}. I'm an automated scheduling assistant. How can I help you today?"


def load_config(path: Path | None = None) -> dict:
    """Read and validate the agent config. Raises ConfigError with a clear message if it is malformed."""
    return validate_config(yaml.safe_load(Path(path or get_settings().config_path).read_text()))


def make_client(settings: Settings) -> OpenAI:
    """OpenAI client with an explicit per-request timeout and bounded retries (SDK default is 600s)."""
    return OpenAI(timeout=settings.llm_timeout_s, max_retries=settings.llm_max_retries)


def build_system_prompt(config: dict, clinic: Clinic) -> str:
    days = [clinic.now.date() + timedelta(days=i) for i in range((clinic.horizon_end.date() - clinic.now.date()).days)]
    calendar = "Calendar: " + "; ".join(f"{d:%a %d %b} = {d.isoformat()}" for d in days)
    prompt = config["core_prompt"].format(
        clinic_name=clinic.name, now=f"{clinic.now:%A %d %B %Y, %H:%M}", calendar=calendar
    )
    rules = config.get("learned_rules") or []
    if rules:
        prompt += "\nLEARNED RULES (from past mistakes)\n" + "\n".join(f"- {r['rule']}" for r in rules)
    return prompt


class Agent:
    def __init__(
        self,
        config: dict | None = None,
        clinic: Clinic | None = None,
        client: OpenAI | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self.config = config or load_config()
        self.session = Session(clinic=clinic or Clinic.load(self.settings.clinic_data_path))
        self.client = client or make_client(self.settings)
        self._clock = time.monotonic  # injectable for tests
        self.model = self.settings.agent_model or self.config["model"]
        self.lock = threading.Lock()  # callers serialise messages per conversation
        self.greeting = GREETING.format(clinic=self.session.clinic.name)
        self.messages: list[dict] = [
            {"role": "system", "content": build_system_prompt(self.config, self.session.clinic)},
            {"role": "assistant", "content": self.greeting},
        ]

    def respond(self, patient_text: str) -> str:
        s = self.session
        if s.ended:
            return ENDED_REPLY
        started = time.perf_counter()
        self._usage = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self._error_type: str | None = None
        reply, outcome = self._run_turn(patient_text)
        log_event(
            log,
            "turn",
            session=s.id,
            turn=s.turn,
            outcome=outcome,
            model=self.model,
            config_version=self.config["version"],
            **self._usage,
            error_type=self._error_type,
            handoff=s.ended,
            urgent=bool(s.handoff and s.handoff["urgent"]),
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return reply

    def _run_turn(self, patient_text: str) -> tuple[str, str]:
        """One patient turn. Returns (reply, outcome code) where every exit path has its own code."""
        s = self.session
        s.turn += 1
        s.patient_messages.append(patient_text)
        self.messages.append({"role": "user", "content": patient_text})

        if s.turn > self.settings.max_turns:
            dispatch(s, "handoff_to_human", {"reason": "conversation turn limit reached", "urgent": False})
            return self._say(TOO_LONG_REPLY), "turn_limit"

        if hit := emergency_match(patient_text):
            dispatch(s, "handoff_to_human", {"reason": f"emergency gate: '{hit}'", "urgent": True})
            return self._say(EMERGENCY_REPLY), "emergency_gate"

        deadline = self._clock() + self.settings.turn_deadline_s
        for _ in range(self.config["max_tool_steps"]):
            if self._clock() > deadline:
                dispatch(s, "handoff_to_human", {"reason": "agent turn deadline exceeded", "urgent": False})
                return self._say(FALLBACK_REPLY), "deadline"
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.config["temperature"],
                    messages=self.messages,
                    tools=TOOL_SCHEMAS,
                    parallel_tool_calls=False,  # one action at a time keeps propose/confirm ordering simple
                )
            except Exception as e:  # noqa: BLE001 - any API failure must end in a handoff, never a stranded patient
                self._error_type = type(e).__name__
                dispatch(s, "handoff_to_human", {"reason": f"agent error: {type(e).__name__}", "urgent": False})
                return self._say(FALLBACK_REPLY), "llm_error"
            self._count_usage(response)
            msg = response.choices[0].message

            if not msg.tool_calls:
                return self._say(msg.content or ""), "handoff" if s.ended else "reply"

            self.messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments},
                        }
                        for c in msg.tool_calls
                    ],
                }
            )
            for c in msg.tool_calls:
                result = dispatch(s, c.function.name, c.function.arguments)
                self.messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})

        dispatch(s, "handoff_to_human", {"reason": "agent exceeded tool step limit", "urgent": False})
        return self._say(FALLBACK_REPLY), "step_limit"

    def _count_usage(self, response) -> None:
        self._usage["llm_calls"] += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            self._usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
            self._usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0

    def _say(self, text: str) -> str:
        self.messages.append({"role": "assistant", "content": text})
        return text

    def transcript(self) -> list[dict]:
        """Patient-visible conversation only (what a transcript-only judge would see)."""
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.messages
            if m["role"] in ("user", "assistant") and m.get("content") and not m.get("tool_calls")
        ]
