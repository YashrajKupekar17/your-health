"""Agent loop tests with a scripted fake LLM: no API key, fully deterministic."""

import json
from types import SimpleNamespace as NS

from yourhealth.agent import FALLBACK_REPLY, TOO_LONG_REPLY, Agent, build_system_prompt, load_config, make_client
from yourhealth.settings import Settings
from yourhealth.clinic import Clinic
from yourhealth.safety import EMERGENCY_REPLY, emergency_match


def tool_call(name, **args):
    return NS(id=f"call_{name}", function=NS(name=name, arguments=json.dumps(args)))


def reply(content=None, calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))])


class FakeLLM:
    """Returns scripted responses in order and records what it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(*responses):
    return Agent(client=FakeLLM(*responses))


def test_emergency_gate_skips_llm():
    a = make()  # any LLM call would fail: no scripted responses
    assert a.respond("I have crushing chest pain right now") == EMERGENCY_REPLY
    assert a.client.calls == 0
    assert a.session.handoff["urgent"] is True
    assert a.session.handoff["caller_words"] == ["I have crushing chest pain right now"]


def test_emergency_gate_fires_mid_booking():
    a = make(reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
             reply("Thanks Priya, what can I book?"))
    a.respond("I'm Priya Sharma, 1990-04-12")
    assert a.respond("actually wait, I suddenly can't breathe") == EMERGENCY_REPLY


def test_full_booking_needs_two_turns():
    a = make(
        reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
        reply(calls=[tool_call("propose_booking", slot_id="P1-20261013-0930", reason="checkup")]),
        reply(calls=[tool_call("confirm_pending")]),  # model tries to commit in the same turn
        reply("Shall I book Tuesday 13 October at 9:30?"),
        reply(calls=[tool_call("confirm_pending")]),
        reply("Done, you're booked."),
    )
    a.respond("Priya Sharma, 12 April 1990, checkup tomorrow 9:30 with Dr Rao")
    assert a.session.tool_log[-1]["result"]["error"] == "needs_patient_confirmation"
    assert len(a.session.clinic.appointments) == 5
    a.respond("yes")
    assert a.session.tool_log[-1]["result"]["done"] == "book"
    assert len(a.session.clinic.appointments) == 6


def test_api_failure_hands_off():
    a = make(RuntimeError("boom"))
    assert a.respond("hi") == FALLBACK_REPLY
    assert a.session.ended


def test_tool_loop_is_capped():
    a = make(*[reply(calls=[tool_call("list_providers", specialty=None)])] * 20)
    assert a.respond("hi") == FALLBACK_REPLY
    assert a.client.calls == load_config()["max_tool_steps"]


def test_learned_rules_reach_prompt():
    cfg = load_config() | {"learned_rules": [{"rule": "Always say hello."}]}
    assert "- Always say hello." in build_system_prompt(cfg, Clinic.load())


def test_gate_phrases():
    for text in ["my chest pain is back", "I want to kill myself", "he passed out", "I CANT BREATHE"]:
        assert emergency_match(text), text
    for text in ["book a checkup", "I need my skin rash looked at", "can I see Dr Chen"]:
        assert not emergency_match(text), text


def test_conversation_turn_limit_hands_off():
    a = Agent(client=FakeLLM(reply("ok"), reply("ok")), settings=Settings(max_turns=2))
    a.respond("one")
    a.respond("two")
    assert a.respond("three") == TOO_LONG_REPLY
    assert a.session.handoff["reason"] == "conversation turn limit reached"


def test_turn_deadline_hands_off():
    a = Agent(client=FakeLLM(*[reply(calls=[tool_call("list_providers", specialty=None)])] * 5),
              settings=Settings(turn_deadline_s=10))
    ticks = iter([0, 4, 8, 12, 16])  # each LLM step "takes" 4 seconds
    a._clock = lambda: next(ticks)
    assert a.respond("hi") == FALLBACK_REPLY
    assert a.session.handoff["reason"] == "agent turn deadline exceeded"
    assert a.client.calls == 2  # deadline at t=10: calls at t=4 and t=8, stops at t=12 (not after 8 steps)


def test_client_has_explicit_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-used")
    c = make_client(Settings(llm_timeout_s=7, llm_max_retries=1))
    assert c.timeout == 7 and c.max_retries == 1
