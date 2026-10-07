"""One improvement cycle: baseline -> propose -> lint -> stage 1 (targets) -> stage 2 (full suite)
-> gate -> human approval -> apply as a new config version, or revert. Every decision is logged.
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path

import yaml
from openai import OpenAI

from evals.runner import RUNS_DIR, config_hash, harness_fingerprint, render_report, run_eval, summarize
from evals.scenario import load_scenarios
from yourhealth.agent import CONFIG_PATH, load_config

from .gate import comparison_table, evaluate
from .propose import collect_failures, propose_rule

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "config" / "history"
LEDGER = ROOT / "improve" / "ledger.jsonl"
IMPROVE_RUNS = ROOT / "runs" / "improve"

_HEADER = """# Agent configuration. The improvement loop may ONLY edit `learned_rules` (and bump `version`).
# Everything else is human-owned policy. Previous versions: config/history/ (python -m improve rollback N).
"""


class _Literal(str):
    pass


yaml.add_representer(_Literal, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"))


def save_config(config: dict, path: Path = CONFIG_PATH) -> None:
    data = {k: (_Literal(v) if isinstance(v, str) and "\n" in v else v) for k, v in config.items()}
    path.write_text(_HEADER + yaml.dump(data, sort_keys=False, allow_unicode=True, width=100))


def archive(config: dict) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    dest = HISTORY_DIR / f"agent_v{config['version']}.yaml"
    save_config(config, dest)
    return dest


def log(entry: dict) -> None:
    with LEDGER.open("a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **entry}) + "\n")


def find_baseline(config: dict, n_scenarios: int) -> Path | None:
    """Most recent full eval run made with exactly this config AND graded by the current scenarios/checks."""
    for d in sorted(RUNS_DIR.glob("*"), reverse=True):
        f = d / "results.json"
        if f.exists():
            r = json.loads(f.read_text())
            if (r["config_hash"] == config_hash(config) and r.get("harness") == harness_fingerprint()
                    and len(r["scenarios"]) == n_scenarios):
                return d
    return None


def run_cycle(yes: bool = False, baseline_dir: Path | None = None, max_attempts: int = 2, trials: int = 3) -> bool:
    client = OpenAI(max_retries=3)
    config = load_config()
    scenarios = load_scenarios()
    version = config["version"]

    baseline_dir = baseline_dir or find_baseline(config, len(scenarios))
    if baseline_dir is None:
        print(f"No baseline for config v{version}; running the full suite first...")
        before = run_eval(config, scenarios, trials, label=f"v{version}-baseline")
        baseline_dir = Path(before["dir"])
    else:
        before = json.loads((baseline_dir / "results.json").read_text())
    print(f"Baseline: {baseline_dir.name}  (train {before['summary']['train']}, holdout {before['summary'].get('holdout')})")

    by_check = collect_failures(before, baseline_dir)
    if not by_check:
        print("No train failures to learn from. Nothing to do.")
        return False

    feedback = ""
    for attempt in range(1, max_attempts + 1):
        p = propose_rule(config, by_check, client, feedback)
        print(f"\n--- Attempt {attempt}: proposed rule (targets {p.fixes}, check '{p.check}')\n  {p.rule}\n  why: {p.why}")
        entry = {"attempt": attempt, "config_version": version, "baseline_run": baseline_dir.name,
                 "rule": p.rule, "why": p.why, "fixes": p.fixes, "check": p.check}

        if p.lint_errors:
            print(f"  REJECTED by lint: {p.lint_errors}")
            log(entry | {"decision": "rejected_lint", "reasons": p.lint_errors})
            feedback = f"rule {p.rule!r} failed lint: {p.lint_errors}"
            continue

        candidate = copy.deepcopy(config)
        candidate["version"] = version + 1
        candidate["learned_rules"] = list(config.get("learned_rules") or []) + [{
            "id": f"R{len(config.get('learned_rules') or []) + 1}", "rule": p.rule, "why": p.why,
            "fixes": p.fixes, "learned_from": baseline_dir.name,
        }]

        # Stage 1: cheap check on the targeted scenarios only.
        target_sc = [s for s in scenarios if s.id in p.fixes]
        stage1 = run_eval(candidate, target_sc, trials, label=f"v{version + 1}-targets")
        g1 = evaluate(before, stage1, p.fixes)
        print(f"  stage 1 (targets only): {'ok' if g1.accepted else g1.reasons}")
        if not g1.accepted:
            log(entry | {"decision": "rejected_stage1", "reasons": g1.reasons, "candidate_run": Path(stage1["dir"]).name})
            feedback = f"rule {p.rule!r} did not fix its targets: {g1.reasons}"
            continue

        # Stage 2: full suite with fresh trials (train + holdout).
        after = run_eval(candidate, scenarios, trials, label=f"v{version + 1}-candidate")
        g = evaluate(before, after, p.fixes)
        if g.drops:
            # 3 trials are noisy: a drop only counts if it reproduces on a fresh re-run.
            print(f"  apparent drop in {g.drops}; confirming with a fresh re-run...")
            confirm = run_eval(candidate, [s for s in scenarios if s.id in g.drops], trials, label=f"v{version + 1}-confirm")
            after["scenarios"].update(confirm["scenarios"])
            after["summary"] = summarize(after)
            g = evaluate(before, after, p.fixes)

        out = IMPROVE_RUNS / f"{datetime.now():%Y%m%d-%H%M%S}-v{version + 1}"
        out.mkdir(parents=True, exist_ok=True)
        table = comparison_table(before, after, p.fixes)
        (out / "comparison.md").write_text(
            f"# Improvement v{version} -> v{version + 1}\n\n**Rule:** {p.rule}\n\n**Why:** {p.why}\n\n"
            f"**Learned from:** `{baseline_dir.name}` (check `{p.check}`)\n\n**Gate:** "
            f"{'passed' if g.accepted else 'FAILED: ' + '; '.join(g.reasons)}\n\n{table}\n\n"
            f"Before: train {before['summary']['train']}, holdout {before['summary'].get('holdout')}\n\n"
            f"After: train {after['summary']['train']}, holdout {after['summary'].get('holdout')}\n")
        (out / "after_report.md").write_text(render_report(after))
        print(f"\n{table}\n")
        entry |= {"candidate_run": Path(after["dir"]).name, "comparison": str(out.relative_to(ROOT))}

        if not g.accepted:
            print(f"  REJECTED by gate: {g.reasons}")
            log(entry | {"decision": "rejected_gate", "reasons": g.reasons})
            feedback = f"rule {p.rule!r} was rejected: {g.reasons}"
            continue

        approved = yes or input("Gate passed. Apply this rule? [y/N] ").strip().lower() == "y"
        if not approved:
            print("  Not applied (human declined).")
            log(entry | {"decision": "rejected_human"})
            return False

        archive(config)  # keep the previous version for rollback
        save_config(candidate)
        archive(candidate)
        log(entry | {"decision": "accepted", "new_version": version + 1})
        print(f"  ACCEPTED: config is now v{version + 1}. Comparison: {out.relative_to(ROOT)}/comparison.md")
        return True

    print("No candidate passed. Config unchanged.")
    return False


def rollback(version: int) -> None:
    src = HISTORY_DIR / f"agent_v{version}.yaml"
    if not src.exists():
        raise SystemExit(f"no archived config v{version} in {HISTORY_DIR}")
    shutil.copy(src, CONFIG_PATH)
    log({"decision": "rollback", "to_version": version})
    print(f"Config rolled back to v{version}.")
