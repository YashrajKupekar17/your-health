"""Run scenarios against the agent: N trials each, in parallel, every trial in a fresh clinic.

Artifacts per run (runs/eval/<run_id>/):
  config.yaml     exact agent config used (so any result can be reproduced)
  results.json    machine-readable results, read by the improvement loop
  report.md       human-readable summary
  traces/         one JSON trace per trial (conversation, tool calls, before/after state)
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml
from openai import OpenAI

from yourhealth.agent import Agent, make_client
from yourhealth.clinic import Clinic
from yourhealth.settings import get_settings

from .checks import CHECKS, run_checks
from .judge import judge_trial
from .scenario import SCENARIOS_PATH, Scenario
from .simulator import PatientSimulator

RUNS_DIR = Path(__file__).resolve().parents[1] / "runs" / "eval"
MAX_TURNS = 12


def models() -> dict:
    return {"sim": os.getenv("SIM_MODEL", "gpt-4.1-mini"), "judge": os.getenv("JUDGE_MODEL", "gpt-4.1")}


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:10]


def harness_fingerprint() -> dict:
    """What a result was graded against. Two runs are only comparable if this matches."""
    return {"scenarios_hash": hashlib.sha256(SCENARIOS_PATH.read_bytes()).hexdigest()[:10],
            "checks": [fn.__name__ for fn in CHECKS]}


def _snapshot(clinic: Clinic) -> dict:
    return copy.deepcopy(clinic.appointments)


def run_trial(sc: Scenario, config: dict, trial: int, client: OpenAI) -> dict:
    clinic = Clinic.load()
    before = _snapshot(clinic)
    agent = Agent(config=config, clinic=clinic, client=client)
    sim = PatientSimulator(sc, client, models()["sim"])
    s = agent.session
    trace = {"scenario": sc.id, "trial": trial, "greeting": agent.greeting, "turns": [], "error": None}

    agent_text = agent.greeting
    try:
        for _ in range(MAX_TURNS):
            patient_text = sim.reply(agent_text)
            if patient_text is None:
                break
            seen = len(s.tool_log)
            agent_text = agent.respond(patient_text)
            trace["turns"].append({"turn": s.turn, "patient": patient_text, "agent": agent_text,
                                   "tools": s.tool_log[seen:], "verified_patient": s.patient_id})
            if s.ended:
                break
    except Exception as e:  # simulator/API failure: the trial is invalid, not an agent failure
        trace["error"] = f"{type(e).__name__}: {e}"

    if s.handoff and str(s.handoff.get("reason", "")).startswith("agent error"):
        trace["error"] = trace["error"] or s.handoff["reason"]
    trace.update(before=before, after=_snapshot(clinic), handoff=s.handoff,
                 patients=copy.deepcopy(clinic.patients))
    return trace


def grade_trial(sc: Scenario, trace: dict, client: OpenAI, use_judge: bool) -> dict:
    checks = run_checks(sc, trace)
    judge = judge_trial(sc, trace, client, models()["judge"]) if use_judge else None
    if trace["error"]:
        outcome = "infra_error"
    elif all(c.passed for c in checks):
        outcome = "pass"
    elif judge and not judge["patient_followed_card"]:
        outcome = "sim_error"  # failed, but the simulated patient went off-script: not the agent's fault
    else:
        outcome = "fail"
    return {"trial": trace["trial"], "outcome": outcome, "checks": [asdict(c) for c in checks], "judge": judge}


def run_eval(config: dict, scenarios: list[Scenario], trials: int = 3, label: str = "run",
             use_judge: bool = True, concurrency: int | None = None, client: OpenAI | None = None) -> dict:
    client = client or make_client(get_settings())
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{label}"
    out = RUNS_DIR / run_id
    (out / "traces").mkdir(parents=True, exist_ok=True)
    (out / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))

    jobs = [(sc, t) for sc in scenarios for t in range(1, trials + 1)]

    def work(job):
        sc, t = job
        trace = run_trial(sc, config, t, client)
        graded = grade_trial(sc, trace, client, use_judge)
        (out / "traces" / f"{sc.id}-t{t}.json").write_text(json.dumps(trace | {"graded": graded}, indent=2, default=str))
        return sc.id, graded

    workers = concurrency or int(os.getenv("EVAL_CONCURRENCY", "8"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        graded = list(pool.map(work, jobs))

    results = {
        "run_id": run_id, "label": label, "config_version": config.get("version"),
        "config_hash": config_hash(config), "agent_model": get_settings().agent_model or config["model"],
        "models": models(), "trials": trials, "harness": harness_fingerprint(), "scenarios": {},
    }
    for sc in scenarios:
        ts = sorted((g for sid, g in graded if sid == sc.id), key=lambda g: g["trial"])
        valid = [g for g in ts if g["outcome"] != "infra_error"]
        passes = sum(g["outcome"] == "pass" for g in valid)
        results["scenarios"][sc.id] = {
            "title": sc.title, "split": sc.split, "critical": sc.critical,
            "passes": passes, "valid": len(valid),
            "all_pass": bool(valid) and passes == len(valid),
            "trials": ts,
        }
    results["summary"] = summarize(results)
    (out / "results.json").write_text(json.dumps(results, indent=2))
    (out / "report.md").write_text(render_report(results))
    results["dir"] = str(out)
    return results


def summarize(results: dict) -> dict:
    summary = {}
    for split in ("train", "holdout"):
        rows = [r for r in results["scenarios"].values() if r["split"] == split]
        if not rows:
            continue
        n_trials = sum(r["valid"] for r in rows)
        summary[split] = {
            "pass_rate": round(sum(r["passes"] for r in rows) / n_trials, 3) if n_trials else None,
            "all_pass": f"{sum(r['all_pass'] for r in rows)}/{len(rows)}",
        }
    crit = [r for r in results["scenarios"].values() if r["critical"]]
    summary["critical_all_pass"] = all(r["all_pass"] for r in crit)
    return summary


def render_report(results: dict) -> str:
    k = results["trials"]
    lines = [
        f"# Eval run `{results['run_id']}`",
        "",
        f"Config v{results['config_version']} (hash `{results['config_hash']}`), agent `{results['agent_model']}`, "
        f"simulator `{results['models']['sim']}`, judge `{results['models']['judge']}`, {k} trials per scenario.",
        "",
        "Pass/fail comes from deterministic checks only. The judge column is advisory.",
        "",
        "| Scenario | Split | Passed | Failing checks | Judge flags |",
        "|---|---|---|---|---|",
    ]
    for sid, r in results["scenarios"].items():
        failing = sorted({c["name"] for t in r["trials"] for c in t["checks"] if not c["passed"]})
        flags = sorted({c["criterion"] for t in r["trials"] if t["judge"]
                        for c in t["judge"]["criteria"] if c.get("verdict") == "fail"})
        other = [t["outcome"] for t in r["trials"] if t["outcome"] in ("sim_error", "infra_error")]
        crit = " ⚠" if r["critical"] else ""
        mark = "✅" if r["all_pass"] else "❌"
        note = f" ({', '.join(other)})" if other else ""
        lines.append(f"| {mark} {sid} {r['title']}{crit} | {r['split']} | {r['passes']}/{r['valid']}{note} | "
                     f"{', '.join(failing) or '-'} | {'; '.join(flags) or '-'} |")
    s = results["summary"]
    lines += ["", "## Summary", ""]
    for split in ("train", "holdout"):
        if split in s:
            lines.append(f"- **{split}**: trial pass rate {s[split]['pass_rate']:.0%}, "
                         f"scenarios passing all {k} trials: {s[split]['all_pass']}")
    lines.append(f"- **critical scenarios all passing**: {'yes' if s['critical_all_pass'] else 'NO'}")
    return "\n".join(lines) + "\n"
