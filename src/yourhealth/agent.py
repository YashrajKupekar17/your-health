"""The agent: one LLM in a tool-calling loop, wrapped by code it cannot bypass.

Per patient message:
  1. turn += 1 (this is what lets confirm_pending tell a later turn from the same turn)
  2. emergency gate (code) -> fixed reply + urgent handoff, LLM never runs
  3. LLM <-> tools loop, capped at max_tool_steps
  4. if the cap is hit or the API fails, hand off rather than leave the patient stuck
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import yaml
from openai import OpenAI

from .clinic import Clinic
from .safety import EMERGENCY_REPLY, emergency_match
from .tools import TOOL_SCHEMAS, Session, dispatch

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "agent.yaml"
FALLBACK_REPLY = "Sorry, I'm having trouble with that. Let me connect you with our front desk."
ENDED_REPLY = "A member of our staff will take it from here."


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


def build_system_prompt(config: dict, clinic: Clinic) -> str:
    days = [clinic.now.date() + timedelta(days=i) for i in range((clinic.horizon_end.date() - clinic.now.date()).days)]
    calendar = "Calendar: " + "; ".join(f"{d:%a %d %b} = {d.isoformat()}" for d in days)
    prompt = config["core_prompt"].format(clinic_name=clinic.name, now=f"{clinic.now:%A %d %B %Y, %H:%M}", calendar=calendar)
    rules = config.get("learned_rules") or []
    if rules:
        prompt += "\nLEARNED RULES (from past mistakes)\n" + "\n".join(f"- {r['rule']}" for r in rules)
    return prompt


class Agent:
    def __init__(self, config: dict | None = None, clinic: Clinic | None = None, client: OpenAI | None = None):
        self.config = config or load_config()
        self.session = Session(clinic=clinic or Clinic.load())
        self.client = client or OpenAI()
        self.model = os.getenv("AGENT_MODEL", self.config["model"])
        self.messages: list[dict] = [{"role": "system", "content": build_system_prompt(self.config, self.session.clinic)}]

    def respond(self, patient_text: str) -> str:
        s = self.session
        if s.ended:
            return ENDED_REPLY
        s.turn += 1
        self.messages.append({"role": "user", "content": patient_text})

        if hit := emergency_match(patient_text):
            dispatch(s, "handoff_to_human", {"reason": f"emergency gate: '{hit}'", "urgent": True})
            return self._say(EMERGENCY_REPLY)

        for _ in range(self.config["max_tool_steps"]):
            try:
                msg = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.config["temperature"],
                    messages=self.messages,
                    tools=TOOL_SCHEMAS,
                    parallel_tool_calls=False,  # one action at a time keeps propose/confirm ordering simple
                ).choices[0].message
            except Exception as e:  # network, rate limit, bad request: never leave the patient hanging
                dispatch(s, "handoff_to_human", {"reason": f"agent error: {type(e).__name__}", "urgent": False})
                return self._say(FALLBACK_REPLY)

            if not msg.tool_calls:
                return self._say(msg.content or "")

            self.messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name, "arguments": c.function.arguments}}
                               for c in msg.tool_calls],
            })
            for c in msg.tool_calls:
                result = dispatch(s, c.function.name, c.function.arguments)
                self.messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})

        dispatch(s, "handoff_to_human", {"reason": "agent exceeded tool step limit", "urgent": False})
        return self._say(FALLBACK_REPLY)

    def _say(self, text: str) -> str:
        self.messages.append({"role": "assistant", "content": text})
        return text

    def transcript(self) -> list[dict]:
        """Patient-visible conversation only (what a transcript-only judge would see)."""
        return [{"role": m["role"], "content": m["content"]} for m in self.messages
                if m["role"] in ("user", "assistant") and m.get("content") and not m.get("tool_calls")]
