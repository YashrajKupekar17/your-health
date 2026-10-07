"""Runtime output guard: checked on the final reply BEFORE the patient sees it.

Two failure modes that matter most and that code can detect cheaply and precisely:
  1. False confirmation: the reply says a change was made ("I've booked you...") but no
     confirm_pending succeeded this turn. This is the most reported production failure of
     scheduling agents; the fix is to check against the system of record before telling the patient.
  2. Identifier leak: the reply contains the date of birth or phone number of a patient who is not
     the verified caller.

Deliberately high-precision: a false block costs a rewrite, so the claim patterns are narrow and the
leak check covers hard identifiers only (free-text appointment reasons would false-positive when
the caller mentions the same words). The eval's claims check uses the same patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .tools import Session

# Phrases that assert a change just happened. "Your appointment is on the 20th" describes existing
# state and must not match. Known limit: novel phrasings slip through.
CLAIM = re.compile(
    r"\b(?:i've|i have|has been|have been|is now|successfully)\s+(?:booked|cancell?ed|rescheduled|moved|confirmed)\b"
    r"|\byou(?:'re| are) all set\b",
    re.IGNORECASE,
)

SAFE_CLAIM_REPLY = "Sorry, I haven't made any change yet. Would you like me to go ahead?"
SAFE_LEAK_REPLY = "Sorry, I can't share details about another patient. Is there anything I can help you with?"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    kind: str | None = None  # "false_claim" | "identifier_leak"
    note: str = ""  # instruction to the model for one rewrite
    safe_reply: str = ""  # used if the rewrite also fails


def claims_change(text: str) -> bool:
    return bool(CLAIM.search(text or ""))


def wrote_this_turn(s: Session) -> bool:
    return any(e["turn"] == s.turn and e["tool"] == "confirm_pending" and e["result"].get("ok") for e in s.tool_log)


def _identifier_forms(patient: dict) -> set[str]:
    dob = date.fromisoformat(patient["dob"])
    return {
        patient["dob"],
        patient["phone"],
        f"{dob.day} {dob:%B %Y}",  # 23 November 1958
        f"{dob:%B} {dob.day}, {dob.year}",  # November 23, 1958
    }


def leaked_identifiers(text: str, s: Session) -> list[str]:
    lowered = (text or "").casefold()
    hits = set()
    for pid, patient in s.clinic.patients.items():
        if pid == s.patient_id:
            continue
        hits |= {x for x in _identifier_forms(patient) if x.casefold() in lowered}
    return sorted(hits)


def check_reply(text: str, s: Session) -> Verdict:
    if leaked_identifiers(text, s):
        return Verdict(
            False,
            "identifier_leak",
            "GUARD: your draft reply contained another patient's date of birth or phone number. "
            "Rewrite it without any details about anyone other than the verified caller.",
            SAFE_LEAK_REPLY,
        )
    if claims_change(text) and not wrote_this_turn(s):
        return Verdict(
            False,
            "false_claim",
            "GUARD: your draft reply said a change was made, but no change was confirmed this turn "
            "(confirm_pending did not succeed). Do not claim it. Either ask the patient to confirm the "
            "proposal, or call confirm_pending if they already clearly agreed.",
            SAFE_CLAIM_REPLY,
        )
    return Verdict(True)
