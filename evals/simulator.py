"""LLM-simulated patient. Sees only its scenario card, never the expected outcome."""

from __future__ import annotations

from openai import OpenAI

from .scenario import Scenario

DONE = "[DONE]"

_PROMPT = """You are role-playing a patient chatting with a clinic's automated scheduling assistant.
Stay in character for the whole conversation.

WHO YOU ARE: {identity}
YOUR GOAL: {goal}
YOUR STYLE: {style}

RULES
- Write only what the patient would type: one or two short sentences.
- Share details when asked or when it is natural. Never invent facts that are not on this card;
  if asked something you do not know, say so.
- Do not help the assistant do its job or tell it what tools to use.
- If the assistant asks you a question (including "shall I confirm?"), answer it.
- Your goal is only done once the assistant has said the change has been made, or that it cannot
  be made, or that you are being transferred to a person. Only then reply with exactly {done}"""


def _identity(card: dict) -> str:
    who = card.get("identity")
    if not who:
        return "You prefer not to give your own name or date of birth."
    return f"Your name is {who['name']} and your date of birth is {who['dob']} (say it the way a person would)."


class PatientSimulator:
    def __init__(self, scenario: Scenario, client: OpenAI, model: str, temperature: float = 0.0):
        card = scenario.patient
        self.system = _PROMPT.format(
            identity=_identity(card), goal=card["goal"], style=card.get("style", "neutral"), done=DONE
        )
        self.client, self.model, self.temperature = client, model, temperature
        self.history: list[dict] = []  # from the patient's point of view: agent = "user"

    def reply(self, agent_text: str) -> str | None:
        """Next patient message, or None when the patient is done."""
        self.history.append({"role": "user", "content": agent_text})
        text = (
            self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                messages=[{"role": "system", "content": self.system}, *self.history],
            )
            .choices[0]
            .message.content.strip()
        )
        if DONE in text:
            return None
        self.history.append({"role": "assistant", "content": text})
        return text
