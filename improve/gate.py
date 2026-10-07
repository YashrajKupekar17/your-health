"""Accept/reject a candidate by comparing per-scenario pass RATES before and after. Pure functions.

Rules (all must hold):
  1. improves:  the targeted scenarios' pass rate goes up in total
  2. no regression: no scenario (target or not, train or holdout) has a lower pass rate. A drop is
     only final after a confirmation re-run pooled with the first run (3 trials are noisy).
  3. safety:    every critical scenario still passes every trial
Rates, not counts, so results with different trial counts (a pooled 6-trial baseline vs a 3-trial
candidate) compare fairly. The holdout is never shown to the proposer, so a holdout gain is
evidence the rule generalises.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from evals.stats import sign_test


@dataclass
class GateResult:
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    drops: list[str] = field(default_factory=list)  # scenarios that appear to regress (re-run to confirm)


def rate(results: dict, sid: str) -> float:
    r = results["scenarios"][sid]
    return r["passes"] / r["valid"] if r["valid"] else 0.0


def pool(a: dict, b: dict) -> dict:
    """Combine two result sets: per-scenario passes and trials add up. Scenarios in only one stay as is."""
    out = copy.deepcopy(a)
    for sid, r in b["scenarios"].items():
        if sid not in out["scenarios"]:
            out["scenarios"][sid] = copy.deepcopy(r)
            continue
        o = out["scenarios"][sid]
        o["passes"] += r["passes"]
        o["valid"] += r["valid"]
        o["all_pass"] = o["all_pass"] and r["all_pass"]
        o["trials"] = o["trials"] + r["trials"]
    return out


def evaluate(before: dict, after: dict, targets: list[str]) -> GateResult:
    reasons, drops = [], []
    if targets:
        tb = sum(rate(before, s) for s in targets)
        ta = sum(rate(after, s) for s in targets)
        if ta <= tb:
            reasons.append(f"targets did not improve (pass rate {tb:.2f} -> {ta:.2f}, summed over targets)")
    for sid, r in after["scenarios"].items():
        if sid not in before["scenarios"]:
            continue
        if rate(after, sid) < rate(before, sid):
            drops.append(sid)
        if r["critical"] and not r["all_pass"]:
            reasons.append(f"critical scenario {sid} no longer passes every trial")
    if drops:
        reasons.append(f"possible regression in {drops}")
    return GateResult(accepted=not reasons, reasons=reasons, drops=drops)


def non_regression(before: dict, after: dict) -> GateResult:
    """For removing a rule (ablation): nothing may get worse; no improvement required."""
    return evaluate(before, after, targets=[])


def comparison_table(before: dict, after: dict, targets: list[str]) -> str:
    lines = ["| Scenario | Split | Before | After | |", "|---|---|---|---|---|"]
    for sid, r in after["scenarios"].items():
        b = before["scenarios"].get(sid)
        if b is None:
            continue
        delta = rate(after, sid) - rate(before, sid)
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
        "judged on the targeted scenarios and the holdout). Rates are compared because the baseline is pooled.",
    ]
    return "\n".join(lines)


def _moves(before: dict, after: dict) -> tuple[int, int]:
    deltas = [rate(after, sid) - rate(before, sid) for sid in after["scenarios"] if sid in before["scenarios"]]
    return sum(d > 0 for d in deltas), sum(d < 0 for d in deltas)
