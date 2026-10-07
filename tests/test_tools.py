import json

import pytest

from yourhealth.clinic import Clinic
from yourhealth.tools import TOOL_SCHEMAS, Session, dispatch


@pytest.fixture
def s():
    return Session(clinic=Clinic.load())


def call(s, name, **args):
    return dispatch(s, name, json.dumps(args))


def verify(s, name="Priya Sharma", dob="1990-04-12"):
    return call(s, "verify_patient", full_name=name, date_of_birth=dob)


def search(s, **kw):
    args = {"date_from": "2026-10-12", "date_to": "2026-10-16", "provider_id": None, "specialty": None, "part_of_day": None}
    return call(s, "search_slots", **(args | kw))


# ---- verification ---------------------------------------------------------------

def test_patient_data_requires_verification(s):
    assert call(s, "list_my_appointments")["error"] == "not_verified"
    assert call(s, "propose_booking", slot_id="P1-20261013-0930", reason="x")["error"] == "not_verified"


def test_verify_tolerates_case_and_spacing_but_not_wrong_dob(s):
    assert verify(s, "  priya   SHARMA ")["ok"]
    s2 = Session(clinic=Clinic.load())
    r = verify(s2, dob="1990-12-04")
    assert r["error"] == "not_verified" and s2.patient_id is None


def test_same_name_disambiguated_by_dob(s):
    verify(s, "John Miller", "1972-01-05")
    assert [a["appointment_id"] for a in call(s, "list_my_appointments")["appointments"]] == ["A1004"]


def test_verification_locks_after_three_failures(s):
    for _ in range(3):
        verify(s, dob="2000-01-01")
    assert verify(s)["error"] == "verification_locked"  # even the correct DOB


def test_verify_result_leaks_no_phi(s):
    r = verify(s)
    assert set(r) == {"ok", "verified", "first_name"}


# ---- search ---------------------------------------------------------------------

def test_search_excludes_booked_blocked_and_past(s):
    ids = [x["slot_id"] for x in search(s, provider_id="P1", date_to="2026-10-13")["slots"]]
    assert "P1-20261012-0900" not in ids  # now is 09:00, not in the future
    assert "P1-20261013-0900" not in ids  # booked by A1005
    assert "P1-20261012-0930" in ids


def test_empty_search_offers_next_available(s):
    r = search(s, specialty="cardiology")  # Dr. Fischer on leave until 26 Oct
    assert r["slots"] == []
    assert r["next_available"]["slot_id"] == "P4-20261026-0900"


def test_search_beyond_horizon_is_refused(s):
    assert search(s, date_from="2026-12-01", date_to="2026-12-05")["error"] == "beyond_horizon"


# ---- two-step writes ------------------------------------------------------------

def test_cannot_propose_and_confirm_in_same_turn(s):
    verify(s)
    assert call(s, "propose_booking", slot_id="P1-20261013-0930", reason="checkup")["ok"]
    assert call(s, "confirm_pending")["error"] == "needs_patient_confirmation"
    assert len(s.clinic.appointments) == 5  # nothing written


def test_book_after_patient_turn(s):
    verify(s)
    call(s, "propose_booking", slot_id="P1-20261013-0930", reason="checkup")
    s.turn += 1  # patient said "yes"
    r = call(s, "confirm_pending")
    assert r["ok"] and r["done"] == "book"
    assert search(s, provider_id="P1", date_to="2026-10-13")["slots"][0]["slot_id"] != "P1-20261013-0930"


def test_confirm_revalidates_slot(s):
    verify(s)
    call(s, "propose_booking", slot_id="P1-20261013-0930", reason="checkup")
    s.clinic.book("PT6", "P1-20261013-0930", "someone else got there first")
    s.turn += 1
    assert call(s, "confirm_pending")["error"] == "slot_unavailable"


def test_cannot_touch_another_patients_appointment(s):
    verify(s)  # Priya
    assert call(s, "propose_cancel", appointment_id="A1001")["error"] == "appointment_not_found"  # John's


def test_change_inside_24h_is_refused(s):
    verify(s, "Maria Garcia", "1958-11-23")
    assert call(s, "propose_cancel", appointment_id="A1002")["error"] == "inside_change_cutoff"


def test_reschedule(s):
    verify(s)
    call(s, "propose_reschedule", appointment_id="A1003", new_slot_id="P3-20261015-1000")
    s.turn += 1
    assert call(s, "confirm_pending")["ok"]
    assert s.clinic.appointments["A1003"]["start"] == "2026-10-15T10:00"


def test_switching_patient_drops_pending(s):
    verify(s)
    call(s, "propose_cancel", appointment_id="A1003")
    verify(s, "Tom Becker", "1979-09-09")
    s.turn += 1
    assert call(s, "confirm_pending")["error"] == "nothing_pending"


# ---- robustness -----------------------------------------------------------------

def test_bad_input_never_raises(s):
    assert call(s, "no_such_tool")["error"] == "unknown_tool"
    assert dispatch(s, "verify_patient", "{not json")["error"] == "bad_arguments"
    assert call(s, "verify_patient", full_name="x")["error"] == "bad_arguments"
    verify(s)
    assert call(s, "propose_booking", slot_id="garbage", reason="x")["error"] == "invalid_slot"


def test_handoff_ends_tool_use(s):
    call(s, "handoff_to_human", reason="chest pain", urgent=True)
    assert verify(s)["error"] == "conversation_ended"


def test_every_schema_is_strict():
    for t in TOOL_SCHEMAS:
        params = t["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])


def test_duplicate_identity_is_never_guessed(s):
    s.clinic.patients["PT7"] = {"id": "PT7", "name": "Priya Sharma", "dob": "1990-04-12", "phone": "555-0199"}
    assert verify(s)["error"] == "ambiguous_identity" and s.patient_id is None


def test_handoff_carries_context(s):
    verify(s)
    call(s, "propose_cancel", appointment_id="A1003")
    call(s, "handoff_to_human", reason="wants a person", urgent=False)
    assert s.handoff["patient_id"] == "PT1" and s.handoff["pending"].startswith("Cancel")
