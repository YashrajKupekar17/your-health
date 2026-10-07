"""Runtime output guard: a false confirmation or another patient's identifier never reaches the patient."""

from tests.test_agent import FakeLLM, reply, tool_call
from yourhealth.agent import Agent
from yourhealth.clinic import Clinic
from yourhealth.guard import SAFE_CLAIM_REPLY, SAFE_LEAK_REPLY, check_reply
from yourhealth.tools import Session


def session(verified=None):
    s = Session(clinic=Clinic.load())
    s.turn = 1
    s.patient_id = verified
    return s


# ---- unit -------------------------------------------------------------------------


def test_claim_without_write_is_blocked():
    v = check_reply("Great, I've booked you for Tuesday at 9:30.", session("PT1"))
    assert not v.ok and v.kind == "false_claim"


def test_claim_after_successful_confirm_passes():
    s = session("PT1")
    s.tool_log.append({"turn": 1, "tool": "confirm_pending", "args": {}, "result": {"ok": True}})
    assert check_reply("I've booked you for Tuesday at 9:30.", s).ok


def test_a_confirm_from_an_earlier_turn_does_not_count():
    s = session("PT1")
    s.tool_log.append({"turn": 1, "tool": "confirm_pending", "args": {}, "result": {"ok": True}})
    s.turn = 2
    assert not check_reply("You're all set!", s).ok


def test_describing_existing_state_is_not_a_claim():
    assert check_reply("Your appointment is on Tuesday 20 October at 11:00.", session("PT1")).ok


def test_other_patients_identifiers_are_blocked_in_any_format():
    s = session("PT1")  # Priya verified; John Miller (PT2) is not
    for text in ["His DOB is 1985-07-30.", "born July 30, 1985", "born 30 July 1985", "call 555-0102"]:
        v = check_reply(text, s)
        assert not v.ok and v.kind == "identifier_leak", text


def test_verified_callers_own_identifiers_are_fine():
    assert check_reply("Thanks, I have your date of birth as 12 April 1990.", session("PT1")).ok


# ---- in the agent loop ------------------------------------------------------------


def test_false_claim_is_rewritten_before_the_patient_sees_it():
    a = Agent(
        client=FakeLLM(
            reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
            reply("You're all set for Tuesday at 9:30!"),  # nothing was booked
            reply("I can book Tuesday at 9:30. Shall I go ahead?"),  # rewrite after the guard note
        )
    )
    out = a.respond("Priya Sharma, 1990-04-12, book me Tuesday 9:30")
    assert out == "I can book Tuesday at 9:30. Shall I go ahead?"
    assert not any("all set" in (m.get("content") or "") for m in a.messages)  # blocked draft never kept
    assert any(m["role"] == "system" and m["content"].startswith("GUARD:") for m in a.messages)


def test_repeated_false_claim_falls_back_to_a_safe_reply():
    a = Agent(client=FakeLLM(reply("I've booked it."), reply("It has been booked.")))
    assert a.respond("book me anything") == SAFE_CLAIM_REPLY
    assert len(a.session.clinic.appointments) == 5


def test_leak_falls_back_without_repeating_the_identifier():
    a = Agent(client=FakeLLM(reply("John was born 1985-07-30."), reply("His birthday is July 30, 1985.")))
    out = a.respond("when is John Miller's birthday?")
    assert out == SAFE_LEAK_REPLY and "1985" not in out


def test_progress_labels_are_emitted_per_tool_without_data():
    labels = []
    a = Agent(
        client=FakeLLM(
            reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
            reply(calls=[tool_call("list_my_appointments")]),
            reply("You have one appointment."),
        )
    )
    a.respond("Priya Sharma 1990-04-12, what do I have booked?", on_progress=labels.append)
    assert labels == ["Verifying your details…", "Looking up your appointments…"]
    assert not any("Priya" in label for label in labels)


def test_broken_progress_listener_does_not_break_the_turn():
    def boom(_):
        raise RuntimeError("listener down")

    a = Agent(client=FakeLLM(reply(calls=[tool_call("list_providers", specialty=None)]), reply("We have four.")))
    assert a.respond("who works there?", on_progress=boom) == "We have four."
