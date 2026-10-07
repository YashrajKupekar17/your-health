"""One improvement cycle: baseline -> propose -> lint -> stage 1 (targets) -> stage 2 (full suite)
-> gate -> human approval -> apply as a new config version, or revert. Every decision is logged.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from evals.runner import RUNS_DIR, config_hash, harness_fingerprint, models, render_report, run_eval, summarize
from evals.scenario import load_scenarios
from yourhealth.agent import load_config, make_client
from yourhealth.settings import get_settings, validate_config

from .gate import comparison_table, evaluate, pool
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


def save_config(config: dict, path: Path | None = None) -> None:
    config = validate_config(config)  # never write a config the agent cannot load
    path = path or get_settings().config_path
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
            if (
                r["config_hash"] == config_hash(config)
                and r.get("harness") == harness_fingerprint()
                and len(r["scenarios"]) == n_scenarios
            ):
                return d
    return None


def _models(config: dict) -> dict:
    m = models()
    return {
        "agent": get_settings().agent_model or config["model"],
        "proposer": os.getenv("PROPOSER_MODEL", "gpt-4.1"),
        "simulator": m["sim"],
        "judge": m["judge"],
    }


def recent_rejections(limit: int = 8) -> list[dict]:
    """Rules rejected in earlier cycles, so the proposer does not re-propose them (the loop's memory)."""
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        e = json.loads(line)
        if str(e.get("decision", "")).startswith("rejected") and e.get("rule"):
            out.append({"rule": e["rule"], "reason": "; ".join(e.get("reasons") or [e["decision"]])})
    return out[-limit:]


def _confirm_drops(
    config: dict, scenarios: list, results: dict, before: dict, targets: list[str], trials: int, label: str
):
    """3 trials are noisy: re-run any scenario that looks worse and pool, so a drop must reproduce."""
    g = evaluate(before, results, targets)
    if not g.drops:
        return results, g
    print(f"  apparent drop in {g.drops}; confirming with a fresh re-run...")
    confirm = run_eval(config, [s for s in scenarios if s.id in g.drops], trials, label=label)
    results = pool(results, confirm)
    results["summary"] = summarize(results)
    return results, evaluate(before, results, targets)


def run_cycle(yes: bool = False, baseline_dir: Path | None = None, max_attempts: int = 2, trials: int = 3) -> bool:
    client = make_client(get_settings())
    config = load_config()
    scenarios = load_scenarios()
    version = config["version"]
    models_used = _models(config)

    baseline_dir = baseline_dir or find_baseline(config, len(scenarios))
    if baseline_dir is None:
        print(f"No baseline for config v{version}; running the full suite first...")
        before = run_eval(config, scenarios, trials, label=f"v{version}-baseline")
        baseline_dir = Path(before["dir"])
    else:
        before = json.loads((baseline_dir / "results.json").read_text())
    _banner(1, f"RUN  eval of config v{version}: {baseline_dir.name}")
    print(f"  train {before['summary']['train']}, holdout {before['summary'].get('holdout')}")
    _print_failures(before)

    by_check = collect_failures(before, baseline_dir)
    if not by_check:
        print("No train failures to learn from. Nothing to do.")
        return False

    # Scenarios were picked BECAUSE they failed in the baseline, so a re-run "improves" some of them by
    # chance (regression to the mean). Re-run them on the current config now, pool with the baseline,
    # and only learn from failures that reproduce. Candidates are compared against this pooled baseline.
    failing = sorted({i["scenario"] for items in by_check.values() for i in items})
    print(f"  re-running {failing} to check the failures are real, not luck...")
    fresh = run_eval(config, [s for s in scenarios if s.id in failing], trials, label=f"v{version}-rebaseline")
    before = pool(before, fresh)
    before["summary"] = summarize(before)
    reproduced = {sid for sid in failing if not fresh["scenarios"][sid]["all_pass"]}
    if dropped := sorted(set(failing) - reproduced):
        print(f"  not reproduced on re-run (treated as noise): {dropped}")
    print(f"  confirmed failures: {sorted(reproduced) or 'none'}")
    by_check = {c: [i for i in items if i["scenario"] in reproduced] for c, items in by_check.items()}
    by_check = {c: items for c, items in by_check.items() if items}

    feedback = ""
    for attempt in range(1, max_attempts + 1):
        if not by_check:
            print("No reproducible prompt-fixable failures left.")
            break
        p = propose_rule(config, by_check, client, feedback, rejected=recent_rejections())
        entry = {
            "attempt": attempt,
            "config_version": version,
            "baseline_run": baseline_dir.name,
            "rebaseline_run": Path(fresh["dir"]).name,
            "fix_type": p.fix_type,
            "rule": p.rule,
            "why": p.why,
            "fixes": p.fixes,
            "check": p.check,
            "models": models_used,
        }

        if p.fix_type != "prompt":
            # Not the loop's call: a tool, code or eval change goes to a human as a ticket.
            print(f"\n--- Attempt {attempt}: '{p.check}' needs a {p.fix_type} fix, not a prompt rule\n  {p.note}")
            log(entry | {"decision": "needs_human", "note": p.note})
            by_check.pop(p.check if p.check in by_check else next(iter(by_check)))
            continue

        _banner(2, f"IMPROVEMENT  attempt {attempt}: one rule for '{p.check}' (targets {p.fixes})")
        print(f"  rule: {p.rule}\n  why:  {p.why}")
        if p.lint_errors:
            print(f"  REJECTED by lint: {p.lint_errors}")
            log(entry | {"decision": "rejected_lint", "reasons": p.lint_errors})
            feedback = f"rule {p.rule!r} failed lint: {p.lint_errors}"
            continue

        candidate = copy.deepcopy(config)
        candidate["version"] = version + 1
        candidate["learned_rules"] = list(config.get("learned_rules") or []) + [
            {
                "id": rule_id_for(version + 1),
                "rule": p.rule,
                "why": p.why,
                "fixes": p.fixes,
                "learned_from": baseline_dir.name,
            }
        ]

        _banner(3, f"RE-RUN  candidate v{version + 1} = current rules + this rule")
        # Stage 1: cheap check on the targeted scenarios only.
        target_sc = [s for s in scenarios if s.id in p.fixes]
        stage1 = run_eval(candidate, target_sc, trials, label=f"v{version + 1}-targets")
        stage1, g1 = _confirm_drops(
            candidate, scenarios, stage1, before, p.fixes, trials, f"v{version + 1}-targets-confirm"
        )
        print(f"  stage 1 (targets only): {'ok' if g1.accepted else g1.reasons}")
        if not g1.accepted:
            log(
                entry
                | {"decision": "rejected_stage1", "reasons": g1.reasons, "candidate_run": Path(stage1["dir"]).name}
            )
            feedback = f"rule {p.rule!r} did not fix its targets: {g1.reasons}"
            continue

        # Stage 2: full suite with fresh trials (train + holdout).
        after = run_eval(candidate, scenarios, trials, label=f"v{version + 1}-candidate")
        after, g = _confirm_drops(candidate, scenarios, after, before, p.fixes, trials, f"v{version + 1}-confirm")

        out = IMPROVE_RUNS / f"{datetime.now():%Y%m%d-%H%M%S}-v{version + 1}"
        out.mkdir(parents=True, exist_ok=True)
        table = comparison_table(before, after, p.fixes)
        (out / "comparison.md").write_text(
            f"# Improvement v{version} -> v{version + 1}\n\n**Rule:** {p.rule}\n\n**Why:** {p.why}\n\n"
            f"**Learned from:** `{baseline_dir.name}` + re-run `{Path(fresh['dir']).name}` (check `{p.check}`)\n\n"
            f"**Gate:** {'passed' if g.accepted else 'FAILED: ' + '; '.join(g.reasons)}\n\n{table}\n\n"
            f"Before (pooled): train {before['summary']['train']}, holdout {before['summary'].get('holdout')}\n\n"
            f"After: train {after['summary']['train']}, holdout {after['summary'].get('holdout')}\n"
        )
        (out / "after_report.md").write_text(render_report(after))
        _banner(4, "SCORE  before -> after (rates; the baseline is pooled)")
        print(f"\n{table}\n")
        entry |= {"candidate_run": Path(after["dir"]).name, "comparison": str(out.relative_to(ROOT))}

        if not g.accepted:
            print(f"  REJECTED by gate: {g.reasons}")
            log(entry | {"decision": "rejected_gate", "reasons": g.reasons})
            feedback = f"rule {p.rule!r} was rejected: {g.reasons}"
            continue

        approved, approved_by = _approve(yes, "Gate passed. Apply this rule?")
        if not approved:
            print("  Not applied (human declined).")
            log(entry | {"decision": "rejected_human", "approved_by": approved_by})
            return False

        archive(config)  # keep the previous version for rollback
        save_config(candidate)
        archive(candidate)
        log(entry | {"decision": "accepted", "new_version": version + 1, "approved_by": approved_by})
        print(f"\n  APPLIED: config is now v{version + 1}. Evidence: {out.relative_to(ROOT)}/comparison.md")
        return True

    print("No candidate passed. Config unchanged.")
    return False


def rule_id_for(new_version: int) -> str:
    """Rule ids come from the config version that introduced them, so a retired id is never reused."""
    return f"R{new_version}"


def _banner(step: int, title: str) -> None:
    print(f"\n{'=' * 78}\n[{step}/4] {title}\n{'=' * 78}")


def _print_failures(results: dict) -> None:
    """The eval flagging failures: which train scenarios failed, how often, and on which check."""
    bad = [(sid, r) for sid, r in results["scenarios"].items() if r["split"] == "train" and not r["all_pass"]]
    if not bad:
        print("  no train failures flagged")
    for sid, r in bad:
        checks = sorted({c["name"] for t in r["trials"] for c in t["checks"] if not c["passed"]})
        print(
            f"  FAILED {sid} {r['title']}: {r['passes']}/{r['valid']} passed  (check: {', '.join(checks) or r['trials'][0]['outcome']})"
        )


def _approve(yes: bool, question: str) -> tuple[bool, str]:
    if yes:
        return True, "auto (--yes)"
    return input(f"{question} [y/N] ").strip().lower() == "y", "human"


def ablate(rule_id: str, yes: bool = False, trials: int = 3) -> bool:
    """Does a learned rule still earn its place? Run the suite without it; retire it if nothing gets worse.

    Rules are prompt-level soft fixes; once a proper fix lands in code (e.g. a tool limit), the rule
    may be dead weight. Removing it goes through the same no-regression gate and the same approval.
    """
    config = load_config()
    scenarios = load_scenarios()
    rules = config.get("learned_rules") or []
    if rule_id not in {r["id"] for r in rules}:
        raise SystemExit(f"no learned rule {rule_id}; have {[r['id'] for r in rules]}")
    version = config["version"]
    before_dir = find_baseline(config, len(scenarios))
    before = (
        json.loads((before_dir / "results.json").read_text())
        if before_dir
        else run_eval(config, scenarios, trials, label=f"v{version}-baseline")
    )
    candidate = copy.deepcopy(config)
    candidate["version"] = version + 1
    candidate["learned_rules"] = [r for r in rules if r["id"] != rule_id]
    after = run_eval(candidate, scenarios, trials, label=f"v{version + 1}-without-{rule_id}")
    after, g = _confirm_drops(
        candidate, scenarios, after, before, [], trials, f"v{version + 1}-without-{rule_id}-confirm"
    )
    table = comparison_table(before, after, [])
    print(table)
    out = IMPROVE_RUNS / f"{datetime.now():%Y%m%d-%H%M%S}-ablate-{rule_id}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.md").write_text(
        f"# Ablation: v{version} without {rule_id}\n\n**Gate (non-regression):** "
        f"{'passed' if g.accepted else 'FAILED: ' + '; '.join(g.reasons)}\n\n{table}\n"
    )
    entry = {
        "config_version": version,
        "ablated": rule_id,
        "candidate_run": Path(after["dir"]).name,
        "models": _models(config),
    }
    if not g.accepted:
        print(f"  KEEP {rule_id}: removing it regresses {g.reasons}")
        log(entry | {"decision": "rule_kept", "reasons": g.reasons})
        return False
    approved, approved_by = _approve(yes, f"Nothing regressed without {rule_id}. Retire it?")
    if not approved:
        log(entry | {"decision": "rule_kept", "reasons": ["human declined"], "approved_by": approved_by})
        return False
    archive(config)
    save_config(candidate)
    archive(candidate)
    log(entry | {"decision": "rule_retired", "new_version": version + 1, "approved_by": approved_by})
    print(f"  RETIRED {rule_id}: config is now v{version + 1}.")
    return True


def rollback(version: int) -> None:
    src = HISTORY_DIR / f"agent_v{version}.yaml"
    if not src.exists():
        raise SystemExit(f"no archived config v{version} in {HISTORY_DIR}")
    validate_config(yaml.safe_load(src.read_text()))
    shutil.copy(src, get_settings().config_path)
    log({"decision": "rollback", "to_version": version})
    print(f"Config rolled back to v{version}.")
