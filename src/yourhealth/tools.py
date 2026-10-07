"""Tools the LLM can call, and the session state that gates them.

Design rules enforced here (in code, not in the prompt):
  1. No patient data is read or changed until the caller verifies name + date of birth.
  2. Every write is two steps: propose_* records a pending action, confirm_pending
     executes it. Confirm is only accepted on a LATER patient turn than the proposal,
     so the model cannot propose and commit before the patient has said anything.
  3. Tool results never contain other patients' data, phone numbers or DOBs.
  4. Errors are returned to the model as data ({"ok": false, ...}) with a hint on what
     to do next, so it can recover instead of crashing or improvising.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date

from .clinic import Clinic, ClinicError
from .logs import get_logger, log_event

log = get_logger("tools")

MAX_VERIFY_ATTEMPTS = 3
HANDOFF_QUOTE_MESSAGES = 3


@dataclass
class Pending:
    kind: str  # "book" | "reschedule" | "cancel"
    args: dict
    proposed_turn: int
    summary: str


@dataclass
class Session:
    clinic: Clinic
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    turn: int = 0  # incremented by the agent on every patient message
    patient_id: str | None = None  # set only by a successful verify_patient
    failed_verifications: int = 0
    pending: Pending | None = None
    handoff: dict | None = None  # set by handoff_to_human; ends the conversation
    tool_log: list[dict] = field(default_factory=list)
    patient_messages: list[str] = field(default_factory=list)  # verbatim, for the staff handoff

    @property
    def ended(self) -> bool:
        return self.handoff is not None


def ok(**data) -> dict:
    return {"ok": True, **data}


def err(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ClinicError("invalid_date", f"'{value}' is not a date in YYYY-MM-DD format.") from None


def _require_verified(s: Session) -> dict | None:
    if s.patient_id is None:
        return err(
            "not_verified",
            "Verify the patient first: ask for their full name and date of birth, then call verify_patient.",
        )
    return None


# ---- tool implementations ----------------------------------------------------


def verify_patient(s: Session, full_name: str, date_of_birth: str) -> dict:
    if s.failed_verifications >= MAX_VERIFY_ATTEMPTS:
        return err(
            "verification_locked", "Too many failed attempts. Do not try again; offer to transfer to the front desk."
        )
    matches = s.clinic.find_patients(full_name, date_of_birth)
    if len(matches) > 1:
        # Two records share name + DOB: picking one risks acting on the wrong patient.
        return err("ambiguous_identity", "More than one record matches. Do not guess; transfer to the front desk.")
    if not matches:
        s.failed_verifications += 1
        # Same message for "no such name" and "wrong DOB": never confirm a name exists.
        return err(
            "not_verified",
            "No record matches that name and date of birth. Ask the patient to check both. "
            "Do not say whether the name exists. New patients must register with the front desk.",
        )
    patient = matches[0]
    if s.patient_id != patient["id"]:
        s.pending = None  # never carry a proposal across to a different patient
    s.patient_id = patient["id"]
    return ok(verified=True, first_name=patient["name"].split()[0])


def list_providers(s: Session, specialty: str | None) -> dict:
    return ok(providers=s.clinic.list_providers(specialty))


def search_slots(
    s: Session, date_from: str, date_to: str, provider_id: str | None, specialty: str | None, part_of_day: str | None
) -> dict:
    return ok(
        **s.clinic.search_slots(_parse_date(date_from), _parse_date(date_to), provider_id, specialty, part_of_day)
    )


def list_my_appointments(s: Session) -> dict:
    if e := _require_verified(s):
        return e
    return ok(appointments=s.clinic.upcoming_appointments(s.patient_id))


def _propose(s: Session, kind: str, args: dict, summary: str) -> dict:
    s.pending = Pending(kind, args, s.turn, summary)
    return ok(
        pending=summary,
        next_step="Read this back to the patient and ask for an explicit yes. "
        "Only after they confirm, call confirm_pending.",
    )


def propose_booking(s: Session, slot_id: str, reason: str) -> dict:
    if e := _require_verified(s):
        return e
    provider_id, start = s.clinic.check_bookable(s.patient_id, slot_id)
    slot = s.clinic.describe_slot(provider_id, start)
    return _propose(
        s,
        "book",
        {"slot_id": slot_id, "reason": reason},
        f"Book {slot['provider']} on {slot['start']} (reason: {reason}).",
    )


def propose_reschedule(s: Session, appointment_id: str, new_slot_id: str) -> dict:
    if e := _require_verified(s):
        return e
    old = s.clinic.describe_appointment(s.clinic.check_changeable(s.patient_id, appointment_id))
    provider_id, start = s.clinic.check_bookable(s.patient_id, new_slot_id)
    new = s.clinic.describe_slot(provider_id, start)
    return _propose(
        s,
        "reschedule",
        {"appointment_id": appointment_id, "slot_id": new_slot_id},
        f"Move the appointment with {old['provider']} on {old['start']} to {new['provider']} on {new['start']}.",
    )


def propose_cancel(s: Session, appointment_id: str) -> dict:
    if e := _require_verified(s):
        return e
    old = s.clinic.describe_appointment(s.clinic.check_changeable(s.patient_id, appointment_id))
    return _propose(
        s,
        "cancel",
        {"appointment_id": appointment_id},
        f"Cancel the appointment with {old['provider']} on {old['start']}.",
    )


def confirm_pending(s: Session) -> dict:
    if e := _require_verified(s):
        return e
    p = s.pending
    if p is None:
        return err("nothing_pending", "There is no proposed action. Use a propose_* tool first.")
    if s.turn <= p.proposed_turn:
        return err(
            "needs_patient_confirmation",
            "The patient has not replied since this was proposed. "
            "Read the proposal back and wait for their explicit yes.",
        )
    # Re-validated inside the clinic call: the world may have changed since the proposal.
    if p.kind == "book":
        a = s.clinic.book(s.patient_id, p.args["slot_id"], p.args["reason"])
    elif p.kind == "reschedule":
        a = s.clinic.reschedule(s.patient_id, p.args["appointment_id"], p.args["slot_id"])
    else:
        a = s.clinic.cancel(s.patient_id, p.args["appointment_id"])
    s.pending = None
    return ok(done=p.kind, appointment=s.clinic.describe_appointment(a))


def handoff_to_human(s: Session, reason: str, urgent: bool) -> dict:
    # What a human picking this up needs: why it stopped, in the caller's own words, who (if
    # verified), what was in flight, and the last rule the system enforced (not the model's account).
    last_refusal = next((e["result"]["error"] for e in reversed(s.tool_log) if not e["result"].get("ok")), None)
    s.handoff = {
        "reason": reason,
        "urgent": urgent,
        "patient_id": s.patient_id,
        "pending": s.pending.summary if s.pending else None,
        "turn": s.turn,
        "caller_words": s.patient_messages[-HANDOFF_QUOTE_MESSAGES:],
        "last_refusal": last_refusal,
    }
    s.pending = None
    return ok(transferred=True, next_step="Tell the patient a staff member will take over, then stop.")


# ---- registry: schemas the model sees ------------------------------------------


def _fn(name: str, description: str, props: dict, impl) -> tuple[str, dict, object]:
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            },
        },
    }
    return name, schema, impl


STR = {"type": "string"}
OPT_STR = {"type": ["string", "null"]}

_REGISTRY = [
    _fn(
        "verify_patient",
        "Verify the caller's identity. Required before reading or changing any appointment. "
        "Use the full name and date of birth exactly as the patient gave them.",
        {"full_name": STR, "date_of_birth": {"type": "string", "description": "YYYY-MM-DD"}},
        verify_patient,
    ),
    _fn(
        "list_providers",
        "List the clinic's providers, optionally filtered by specialty (general_practice, dermatology, cardiology).",
        {"specialty": OPT_STR},
        list_providers,
    ),
    _fn(
        "search_slots",
        "Find free appointment slots in a date range. Only offer slots returned by this tool.",
        {
            "date_from": {"type": "string", "description": "YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "YYYY-MM-DD"},
            "provider_id": OPT_STR,
            "specialty": OPT_STR,
            "part_of_day": {"type": ["string", "null"], "enum": ["morning", "afternoon", None]},
        },
        search_slots,
    ),
    _fn("list_my_appointments", "List the verified patient's upcoming appointments.", {}, list_my_appointments),
    _fn(
        "propose_booking",
        "Propose booking a slot for the verified patient. Does NOT book; call confirm_pending "
        "after the patient explicitly agrees.",
        {"slot_id": STR, "reason": STR},
        propose_booking,
    ),
    _fn(
        "propose_reschedule",
        "Propose moving one of the verified patient's appointments to a new slot.",
        {"appointment_id": STR, "new_slot_id": STR},
        propose_reschedule,
    ),
    _fn(
        "propose_cancel",
        "Propose cancelling one of the verified patient's appointments.",
        {"appointment_id": STR},
        propose_cancel,
    ),
    _fn(
        "confirm_pending",
        "Execute the proposed action. Only after the patient explicitly said yes to it.",
        {},
        confirm_pending,
    ),
    _fn(
        "handoff_to_human",
        "Transfer to clinic staff. Use for emergencies (urgent=true), requests outside "
        "scheduling, repeated verification failure, or when the patient asks for a person.",
        {"reason": STR, "urgent": {"type": "boolean"}},
        handoff_to_human,
    ),
]

TOOL_SCHEMAS = [schema for _, schema, _ in _REGISTRY]
_IMPLS = {name: impl for name, _, impl in _REGISTRY}


def dispatch(s: Session, name: str, arguments: str | dict) -> dict:
    """Run one tool call. Never raises: every failure becomes a result the model can read."""
    started = time.perf_counter()
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else arguments
        impl = _IMPLS.get(name)
        if impl is None:
            result = err("unknown_tool", f"There is no tool named '{name}'.")
        elif s.ended:
            result = err("conversation_ended", "The patient has been handed off. Do not call more tools.")
        else:
            result = impl(s, **args)
    except ClinicError as e:
        result = err(e.code, e.message)
    except (json.JSONDecodeError, TypeError) as e:
        args = arguments
        result = err("bad_arguments", f"Invalid arguments for {name}: {e}")
    s.tool_log.append({"turn": s.turn, "tool": name, "args": args, "result": result})
    log_event(
        log,
        "tool_call",
        session=s.id,
        turn=s.turn,
        tool=name,
        ok=result["ok"],
        error=result.get("error"),
        ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return result
