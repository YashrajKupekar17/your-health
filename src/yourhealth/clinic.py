"""The clinic "backend": providers, schedules, patients, appointments.

This module knows nothing about conversations or LLMs. It answers questions about
the schedule and applies changes. Every check that must hold regardless of what the
agent says (slot is free, appointment belongs to the patient, change cutoff) lives here.
"""

from __future__ import annotations

import copy
import functools
import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"  # default; see settings
SLOT_MINUTES = 30
PARTS_OF_DAY = {"morning": (0, 12), "afternoon": (12, 24)}


class ClinicError(Exception):
    """A request the clinic refuses. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def slot_id(provider_id: str, start: datetime) -> str:
    return f"{provider_id}-{start:%Y%m%d-%H%M}"


def parse_slot_id(sid: str) -> tuple[str, datetime]:
    try:
        provider_id, day, hhmm = sid.split("-")
        return provider_id, datetime.strptime(day + hhmm, "%Y%m%d%H%M")
    except ValueError:
        raise ClinicError(
            "invalid_slot", f"'{sid}' is not a valid slot id. Use an id returned by search_slots."
        ) from None


def normalize_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _locked(method):
    """Run under the clinic's lock. Writes re-check and write atomically (no double booking when
    conversations race); reads never see a half-applied change. A database replaces this with a
    transaction plus a unique constraint on (provider, start) for booked appointments."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class Clinic:
    def __init__(self, data: dict):
        self._lock = threading.RLock()
        data = copy.deepcopy(data)  # each session gets its own world
        self.name = data["clinic_name"]
        self.now = datetime.fromisoformat(data["now"])
        self.horizon_end = datetime.combine(
            self.now.date() + timedelta(days=data["booking_horizon_days"]), datetime.min.time()
        )
        self.change_cutoff = timedelta(hours=data["change_cutoff_hours"])
        self.providers = {p["id"]: p for p in data["providers"]}
        self.patients = {p["id"]: p for p in data["patients"]}
        self.appointments = {a["id"]: a for a in data["appointments"]}
        self.unavailable = [
            (u["provider_id"], datetime.fromisoformat(u["start"]), datetime.fromisoformat(u["end"]))
            for u in data["unavailable"]
        ]

    @classmethod
    def load(cls, path: Path | None = None) -> Clinic:
        return cls(json.loads(Path(path or DATA_PATH).read_text()))

    # ---- reads -------------------------------------------------------------

    @_locked
    def find_patients(self, name: str, dob: str) -> list[dict]:
        key = normalize_name(name)
        return [p for p in self.patients.values() if normalize_name(p["name"]) == key and p["dob"] == dob.strip()]

    def list_providers(self, specialty: str | None = None) -> list[dict]:
        return [
            {"provider_id": p["id"], "name": p["name"], "specialty": p["specialty"]}
            for p in self.providers.values()
            if specialty is None or p["specialty"] == specialty
        ]

    def _is_free(self, provider_id: str, start: datetime) -> bool:
        provider = self.providers.get(provider_id)
        if provider is None or start <= self.now or start >= self.horizon_end:
            return False
        if start.weekday() not in provider["weekdays"]:
            return False
        day_start = datetime.combine(start.date(), datetime.strptime(provider["start"], "%H:%M").time())
        day_end = datetime.combine(start.date(), datetime.strptime(provider["end"], "%H:%M").time())
        if not (day_start <= start and start + timedelta(minutes=SLOT_MINUTES) <= day_end):
            return False
        if (start - day_start) % timedelta(minutes=SLOT_MINUTES):
            return False
        if any(pid == provider_id and s <= start < e for pid, s, e in self.unavailable):
            return False
        return not any(
            a["provider_id"] == provider_id and a["status"] == "booked" and a["start"] == f"{start:%Y-%m-%dT%H:%M}"
            for a in self.appointments.values()
        )

    def _slots(self, provider_ids: list[str], first_day: date, last_day: date, part_of_day: str | None):
        lo, hi = PARTS_OF_DAY.get(part_of_day, (0, 24))
        day = first_day
        while day <= last_day:
            for pid in provider_ids:
                t = datetime.combine(day, datetime.min.time())
                for _ in range(24 * 60 // SLOT_MINUTES):
                    if lo <= t.hour < hi and self._is_free(pid, t):
                        yield pid, t
                    t += timedelta(minutes=SLOT_MINUTES)
            day += timedelta(days=1)

    def describe_slot(self, provider_id: str, start: datetime) -> dict:
        p = self.providers[provider_id]
        return {
            "slot_id": slot_id(provider_id, start),
            "provider": p["name"],
            "specialty": p["specialty"],
            "start": f"{start:%A %d %B %Y, %H:%M}",
        }

    @_locked
    def search_slots(
        self,
        date_from: date,
        date_to: date,
        provider_id: str | None = None,
        specialty: str | None = None,
        part_of_day: str | None = None,
        limit: int = 8,
    ) -> dict:
        if provider_id is not None and provider_id not in self.providers:
            raise ClinicError("unknown_provider", f"No provider with id '{provider_id}'. Use list_providers.")
        pids = [p["provider_id"] for p in self.list_providers(specialty) if provider_id in (None, p["provider_id"])]
        if not pids:
            raise ClinicError("unknown_specialty", f"No providers for specialty '{specialty}'. Use list_providers.")
        last_bookable = self.horizon_end.date() - timedelta(days=1)
        if date_to < date_from:
            raise ClinicError("invalid_dates", "date_to is before date_from.")
        if date_from > last_bookable:
            raise ClinicError("beyond_horizon", f"Bookings are only open until {last_bookable:%A %d %B %Y}.")
        date_from, date_to = max(date_from, self.now.date()), min(date_to, last_bookable)

        found = [self.describe_slot(pid, t) for pid, t in self._slots(pids, date_from, date_to, part_of_day)]
        result = {"slots": found[:limit], "total_matching": len(found)}
        if not found:
            # Give the agent a real alternative so it never has to invent one.
            nxt = next(self._slots(pids, date_to + timedelta(days=1), last_bookable, part_of_day), None)
            result["next_available"] = self.describe_slot(*nxt) if nxt else None
        return result

    @_locked
    def upcoming_appointments(self, patient_id: str) -> list[dict]:
        mine = [
            a
            for a in self.appointments.values()
            if a["patient_id"] == patient_id
            and a["status"] == "booked"
            and datetime.fromisoformat(a["start"]) > self.now
        ]
        return [self.describe_appointment(a) for a in sorted(mine, key=lambda a: a["start"])]

    def describe_appointment(self, a: dict) -> dict:
        start = datetime.fromisoformat(a["start"])
        return {
            "appointment_id": a["id"],
            "provider": self.providers[a["provider_id"]]["name"],
            "start": f"{start:%A %d %B %Y, %H:%M}",
            "reason": a["reason"],
        }

    # ---- validation (used by both propose and commit) ----------------------

    @_locked
    def check_bookable(self, patient_id: str, sid: str) -> tuple[str, datetime]:
        provider_id, start = parse_slot_id(sid)
        if not self._is_free(provider_id, start):
            raise ClinicError("slot_unavailable", "That slot is not available. Search again and offer real options.")
        clash = [
            a
            for a in self.appointments.values()
            if a["patient_id"] == patient_id and a["status"] == "booked" and a["start"] == f"{start:%Y-%m-%dT%H:%M}"
        ]
        if clash:
            raise ClinicError("patient_double_booked", "The patient already has an appointment at that time.")
        return provider_id, start

    @_locked
    def check_changeable(self, patient_id: str, appointment_id: str) -> dict:
        a = self.appointments.get(appointment_id)
        # Same error whether it does not exist or belongs to someone else: no leaking.
        if a is None or a["patient_id"] != patient_id or a["status"] != "booked":
            raise ClinicError(
                "appointment_not_found", "No such upcoming appointment for this patient. Use list_my_appointments."
            )
        start = datetime.fromisoformat(a["start"])
        if start <= self.now:
            raise ClinicError("appointment_not_found", "That appointment is in the past.")
        if start - self.now < self.change_cutoff:
            raise ClinicError(
                "inside_change_cutoff",
                f"Appointments within {self.change_cutoff.total_seconds() / 3600:.0f} hours cannot be changed by the "
                "assistant. Offer to transfer the patient to the front desk.",
            )
        return a

    # ---- writes ------------------------------------------------------------

    @_locked
    def book(self, patient_id: str, sid: str, reason: str) -> dict:
        provider_id, start = self.check_bookable(patient_id, sid)
        new_id = f"A{1001 + len(self.appointments)}"
        a = {
            "id": new_id,
            "patient_id": patient_id,
            "provider_id": provider_id,
            "start": f"{start:%Y-%m-%dT%H:%M}",
            "reason": reason,
            "status": "booked",
        }
        self.appointments[new_id] = a
        return a

    @_locked
    def cancel(self, patient_id: str, appointment_id: str) -> dict:
        a = self.check_changeable(patient_id, appointment_id)
        a["status"] = "cancelled"
        return a

    @_locked
    def reschedule(self, patient_id: str, appointment_id: str, sid: str) -> dict:
        a = self.check_changeable(patient_id, appointment_id)
        provider_id, start = self.check_bookable(patient_id, sid)
        a["provider_id"], a["start"] = provider_id, f"{start:%Y-%m-%dT%H:%M}"
        return a
