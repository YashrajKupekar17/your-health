"""Logs carry ids, codes and timings, never patient text or identifiers."""

import json
import logging

from tests.test_agent import FakeLLM, reply, tool_call
from yourhealth.agent import Agent
from yourhealth.logs import JsonFormatter, configure_logging

SECRETS = ["Priya", "Sharma", "1990-04-12", "chest pain", "checkup", "P1-20261013-0930"]


def run_conversation():
    a = Agent(
        client=FakeLLM(
            reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
            reply(calls=[tool_call("propose_booking", slot_id="P1-20261013-0930", reason="checkup")]),
            reply("Shall I book Tuesday at 9:30, Priya?"),
        )
    )
    a.respond("I'm Priya Sharma, born 1990-04-12, I need a checkup")
    a.respond("also I've had chest pain")  # emergency gate
    return a


def test_turn_and_tool_events_without_phi(caplog):
    configure_logging("INFO")
    logging.getLogger("yourhealth").propagate = True  # let caplog see records
    with caplog.at_level(logging.INFO, logger="yourhealth"):
        a = run_conversation()
    lines = [JsonFormatter().format(r) for r in caplog.records]
    events = [json.loads(line) for line in lines]
    turns = [e for e in events if e["event"] == "turn"]
    tools = [e for e in events if e["event"] == "tool_call"]
    assert [t["outcome"] for t in turns] == ["reply", "emergency_gate"]
    assert turns[0]["llm_calls"] == 3 and turns[0]["session"] == a.session.id
    assert {t["tool"] for t in tools} >= {"verify_patient", "propose_booking", "handoff_to_human"}
    blob = "\n".join(lines)
    for secret in SECRETS:
        assert secret not in blob, f"log leaked {secret!r}"
