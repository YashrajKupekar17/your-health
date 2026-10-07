"""Scenario definitions, loaded from YAML and validated up front so a typo fails fast."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SCENARIOS_PATH = Path(__file__).resolve().parent / "scenarios.yaml"
SPLITS = {"train", "holdout"}
HANDOFFS = {"none", "urgent", "routine", "any"}
ACTIONS = {"book", "cancel", "reschedule"}
WRITE_KEYS = {"action", "patient", "provider", "appointment", "date", "date_from", "date_to"}


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    split: str
    patient: dict  # the simulator's card: identity, goal, style
    writes: list[dict]  # expected changes to the clinic
    handoff: str
    critical: bool = False
    no_leak: list[str] = field(default_factory=list)
    judge: list[str] = field(default_factory=list)


def _parse(raw: dict) -> Scenario:
    sid = raw.get("id", "?")
    expect = raw.get("expect") or {}
    s = Scenario(
        id=sid,
        title=raw["title"],
        split=raw["split"],
        critical=bool(raw.get("critical", False)),
        patient=raw["patient"],
        writes=expect.get("writes", []),
        handoff=expect.get("handoff", "none"),
        no_leak=[str(x) for x in expect.get("no_leak", [])],
        judge=raw.get("judge", []),
    )
    problems = []
    if s.split not in SPLITS:
        problems.append(f"split must be one of {SPLITS}")
    if s.handoff not in HANDOFFS:
        problems.append(f"handoff must be one of {HANDOFFS}")
    if not s.patient.get("goal"):
        problems.append("patient.goal is required")
    for w in s.writes:
        if w.get("action") not in ACTIONS or set(w) - WRITE_KEYS:
            problems.append(f"bad write matcher {w}")
    if problems:
        raise ValueError(f"scenario {sid}: " + "; ".join(problems))
    return s


def load_scenarios(path: Path = SCENARIOS_PATH, ids: list[str] | None = None, split: str | None = None) -> list[Scenario]:
    scenarios = [_parse(r) for r in yaml.safe_load(Path(path).read_text())]
    seen = [s.id for s in scenarios]
    if len(seen) != len(set(seen)):
        raise ValueError("duplicate scenario ids")
    if ids:
        missing = set(ids) - set(seen)
        if missing:
            raise ValueError(f"unknown scenario ids: {sorted(missing)}")
        scenarios = [s for s in scenarios if s.id in ids]
    if split:
        scenarios = [s for s in scenarios if s.split == split]
    return scenarios
