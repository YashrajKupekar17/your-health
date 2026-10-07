"""Each deterministic check gets a trace it must pass and a trace it must fail. No LLM."""

import copy

import pytest

from evals.checks import check_claims_match_writes, check_max_options, check_end_state, check_handoff, check_no_leak, diff_appointments
from evals.scenario import Scenario, load_scenarios
from yourhealth.clinic import Clinic


def scenario(**kw):
    base = dict(id="T", title="t", split="train", patient={"goal": "g"}, writes=[], handoff="none")
    return Scenario(**(base | kw))


@pytest.fixture
def clinic():
    return Clinic.load()


def trace(clinic, before, turns=(), handoff=None):
    return {"before": before, "after": copy.deepcopy(clinic.appointments), "turns": list(turns),
            "handoff": handoff, "patients": clinic.patients}


def turn(agent, tools=(), verified=None, n=1):
    return {"turn": n, "patient": "...", "agent": agent, "tools": list(tools), "verified_patient": verified}


def test_all_scenarios_load():
    assert len(load_scenarios()) >= 12


def test_diff_detects_book_cancel_reschedule(clinic):
    before = copy.deepcopy(clinic.appointments)
    clinic.book("PT1", "P1-20261019-0900", "checkup")
    clinic.cancel("PT2", "A1001")
    clinic.reschedule("PT1", "A1003", "P3-20261015-1000")
    actions = sorted(c["action"] for c in diff_appointments(before, clinic.appointments))
    assert actions == ["book", "cancel", "reschedule"]


def test_end_state(clinic):
    before = copy.deepcopy(clinic.appointments)
    clinic.book("PT1", "P1-20261019-0900", "checkup")
    want = [{"action": "book", "patient": "PT1", "provider": "P1", "date_from": "2026-10-19", "date_to": "2026-10-23"}]
    assert check_end_state(scenario(writes=want), trace(clinic, before)).passed
    wrong_week = [want[0] | {"date_from": "2026-10-26", "date_to": "2026-10-30"}]
    assert not check_end_state(scenario(writes=wrong_week), trace(clinic, before)).passed
    assert not check_end_state(scenario(writes=[]), trace(clinic, before)).passed  # unexpected write


def test_handoff(clinic):
    t = trace(clinic, clinic.appointments, handoff={"urgent": True})
    assert check_handoff(scenario(handoff="urgent"), t).passed
    assert check_handoff(scenario(handoff="any"), t).passed
    assert not check_handoff(scenario(handoff="none"), t).passed


def test_claim_without_write_fails(clinic):
    confirm_ok = {"tool": "confirm_pending", "args": {}, "result": {"ok": True}}
    good = [turn("Great, I've booked you for Tuesday.", [confirm_ok])]
    bad = [turn("You're all set for Tuesday at 9:30!")]
    describing = [turn("Your appointment is on Tuesday 20 October.")]
    assert check_claims_match_writes(scenario(), trace(clinic, {}, good)).passed
    assert not check_claims_match_writes(scenario(), trace(clinic, {}, bad)).passed
    assert check_claims_match_writes(scenario(), trace(clinic, {}, describing)).passed


def test_no_leak(clinic):
    before = clinic.appointments
    leak = [turn("John has an annual checkup on Wednesday.")]
    assert not check_no_leak(scenario(), trace(clinic, before, leak)).passed
    # Same words are fine once John (PT2) himself is verified.
    assert check_no_leak(scenario(), trace(clinic, before, [turn(leak[0]["agent"], verified="PT2")])).passed
    assert not check_no_leak(scenario(no_leak=["14 October"]), trace(clinic, before, [turn("It's on 14 October")])).passed


def test_max_options(clinic):
    three = [turn("I have 2:00, 2:30 or 3:00 on Wednesday.")]
    six = [turn("Times: 2:00, 2:30, 3:00, 3:30, 4:00 and 4:30.")]
    assert check_max_options(scenario(), trace(clinic, {}, three)).passed
    assert not check_max_options(scenario(), trace(clinic, {}, six)).passed
