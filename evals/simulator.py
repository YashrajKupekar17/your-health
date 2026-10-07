"""LLM-simulated patient. Sees only its scenario card, never the expected outcome.

Two guards against the most common simulator failures (found in our traces and in the literature):
  - volunteering hidden facts early: the card lists them under `do_not_volunteer`
  - quitting early: the runner, not the simulator, decides when a conversation is over (see `insist`)
"""

from __future__ import annotations

from openai import OpenAI

from .cost import tokens_of
from .scenario import Scenario

DONE = "[DONE]"

_PROMPT = """You are role-playing a patient chatting with a clinic's automated scheduling assistant.
Stay in character for the whole conversation.

WHO YOU ARE: {identity}
YOUR GOAL: {goal}
YOUR STYLE: {style}
{hidden}
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
        hidden = card.get("do_not_volunteer") or []
        self.system = _PROMPT.format(
            identity=_identity(card),
            goal=card["goal"],
            style=card.get("style", "neutral"),
            hidden=("DO NOT MENTION these unless the assistant explicitly asks for them:\n" if hidden else "")
            + "".join(f"- {h}\n" for h in hidden),
            done=DONE,
        )
        self.client, self.model, self.temperature = client, model, temperature
        self.history: list[dict] = []  # from the patient's point of view: agent = "user"
        self.tokens = [0, 0]  # (in, out), for the run's cost report

    def reply(self, agent_text: str) -> str | None:
        """Next patient message, or None when the patient is done."""
        self.history.append({"role": "user", "content": agent_text})
        return self._next()

    def insist(self) -> str | None:
        """Called by the runner when the simulator tried to stop while the assistant awaits an answer."""
        self.history.append(
            {"role": "user", "content": "(The assistant is waiting for your answer. Reply as the patient.)"}
        )
        return self._next()

    def _next(self) -> str | None:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[{"role": "system", "content": self.system}, *self.history],
        )
        used = tokens_of(response)
        self.tokens = [self.tokens[0] + used[0], self.tokens[1] + used[1]]
        text = response.choices[0].message.content.strip()
        if DONE in text:
            return None
        self.history.append({"role": "assistant", "content": text})
        return text
