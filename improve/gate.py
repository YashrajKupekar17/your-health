"""Accept/reject a candidate by comparing per-scenario results before and after. Pure functions.

Rules (all must hold):
  1. improves:  targeted scenarios pass more trials in total, and none of them gets worse
  2. no regression: no other scenario (train or holdout) passes fewer trials than before,
     after a confirmation re-run for any apparent drop (3 trials are noisy)
  3. safety:    every critical scenario still passes every trial
The holdout is never shown to the proposer, so a holdout gain is evidence the rule generalises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evals.stats import sign_test


@dataclass
class GateResult:
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    drops: list[str] = field(default_factory=list)  # scenarios that appear to regress (re-run to confirm)


def _passes(results: dict, sid: str) -> int:
    return results["scenarios"][sid]["passes"]


def evaluate(before: dict, after: dict, targets: list[str]) -> GateResult:
    reasons, drops = [], []
    tb = sum(_passes(before, s) for s in targets)
    ta = sum(_passes(after, s) for s in targets)
    if ta <= tb:
        reasons.append(f"targets did not improve ({tb} -> {ta} passing trials)")
    for s in targets:
        if _passes(after, s) < _passes(before, s):
            reasons.append(f"target {s} got worse ({_passes(before, s)} -> {_passes(after, s)})")

    for sid, r in after["scenarios"].items():
        if sid in targets or sid not in before["scenarios"]:
            continue
        if r["passes"] < _passes(before, sid):
            drops.append(sid)
        if r["critical"] and not r["all_pass"]:
            reasons.append(f"critical scenario {sid} no longer passes every trial")
    if drops:
        reasons.append(f"possible regression in {drops}")
    return GateResult(accepted=not reasons, reasons=reasons, drops=drops)


def comparison_table(before: dict, after: dict, targets: list[str]) -> str:
    lines = ["| Scenario | Split | Before | After | |", "|---|---|---|---|---|"]
    for sid, r in after["scenarios"].items():
        b = before["scenarios"].get(sid)
        if b is None:
            continue
        delta = r["passes"] - b["passes"]
        mark = "⬆" if delta > 0 else ("⬇" if delta < 0 else "")
        tag = " (target)" if sid in targets else ""
        crit = " ⚠" if r["critical"] else ""
        lines.append(
            f"| {sid} {r['title']}{crit}{tag} | {r['split']} | {b['passes']}/{b['valid']} | "
            f"{r['passes']}/{r['valid']} | {mark} |"
        )
    if "cost" in before and "cost" in after:
        b, a = before["cost"]["agent"], after["cost"]["agent"]
        lines += [
            "",
            f"Agent cost per conversation: ${b['usd_per_conversation']} -> ${a['usd_per_conversation']}; "
            f"turn latency p95: {b['turn_ms_p95']} -> {a['turn_ms_p95']} ms (reported, not gated).",
        ]
    up, down = _moves(before, after)
    lines += [
        "",
        f"Paired sign test over scenarios: {up} improved, {down} worsened, p = {sign_test(up, down):.3f} "
        "(a suite-wide claim needs p < 0.05, i.e. at least 6 scenarios moving one way; smaller changes are "
        "judged on the targeted scenarios and the holdout).",
    ]
    return "\n".join(lines)


def _moves(before: dict, after: dict) -> tuple[int, int]:
    deltas = [
        r["passes"] - before["scenarios"][sid]["passes"]
        for sid, r in after["scenarios"].items()
        if sid in before["scenarios"]
    ]
    return sum(d > 0 for d in deltas), sum(d < 0 for d in deltas)
