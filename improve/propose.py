"""Turn eval failures into ONE structured, linted rule for the prompt's learned_rules section.

What the proposer sees: train-split failures only (never holdout, never the judge rubric),
each with the failed check's name and detail and the agent turn that broke it. What it may
change: nothing but a new learned rule. Core policy, tools and code are out of its reach.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

from yourhealth.clinic import Clinic

MAX_RULE_WORDS = 40
MAX_RULES = 6

_PROMPT = """You improve a clinic scheduling assistant. Below are its current instructions and the
evaluation failures from its latest run. Propose ONE new rule to add to its instructions that
would prevent the most important failure pattern. Look at the exchanges to find WHEN it happens:
restating an instruction the assistant already ignores will not change its behaviour; name the
situation that triggers the mistake and the concrete behaviour to use instead.

The rule must be general behaviour guidance (it will apply to every future patient), written as an
instruction to the assistant. Do not mention specific patients, dates, times, ids or test scenarios.
At most {max_words} words. Do not repeat or contradict existing instructions; never weaken safety,
verification or confirmation rules.

CURRENT INSTRUCTIONS:
{core_prompt}

CURRENT LEARNED RULES:
{rules}

FAILURES (grouped by the check that failed):
{failures}
{feedback}
Reply as JSON: {{"rule": str, "why": str, "fixes": [scenario ids this should fix], "check": the failed check name it targets}}"""


@dataclass
class Proposal:
    rule: str
    why: str
    fixes: list[str]
    check: str
    lint_errors: list[str] = field(default_factory=list)


def _failing_turn(trace: dict, check: dict) -> str:
    """The exchange where the check broke (or the last one), so the proposer sees the behaviour, not a label."""
    m = re.match(r"turn (\d+):", check["detail"])
    turns = trace["turns"]
    t = next((x for x in turns if m and x["turn"] == int(m.group(1))), turns[-1] if turns else None)
    if t is None:
        return ""
    tools = ", ".join(f"{e['tool']}({json.dumps(e['args'])})" for e in t["tools"]) or "none"
    return f'patient: "{t["patient"]}" | tools: {tools} | assistant: "{t["agent"]}"'


def collect_failures(results: dict, run_dir: Path) -> dict[str, list[dict]]:
    """Train-split agent failures, grouped by failed check. Simulator/infra errors are excluded."""
    by_check: dict[str, list[dict]] = {}
    for sid, r in results["scenarios"].items():
        if r["split"] != "train":
            continue
        for t in r["trials"]:
            if t["outcome"] != "fail":
                continue
            trace = json.loads((run_dir / "traces" / f"{sid}-t{t['trial']}.json").read_text())
            for c in t["checks"]:
                if not c["passed"]:
                    by_check.setdefault(c["name"], []).append(
                        {
                            "scenario": sid,
                            "title": r["title"],
                            "trial": t["trial"],
                            "detail": c["detail"],
                            "exchange": _failing_turn(trace, c),
                        }
                    )
    return by_check


def _render_failures(by_check: dict[str, list[dict]], limit_per_check: int = 4) -> str:
    out = []
    for check, items in sorted(by_check.items(), key=lambda kv: -len(kv[1])):
        out.append(f"## {check}: {len(items)} failed trials across {sorted({i['scenario'] for i in items})}")
        for i in items[:limit_per_check]:
            out.append(
                f"- {i['scenario']} ({i['title']}), trial {i['trial']}: {i['detail'][:300]}\n  {i['exchange'][:900]}"
            )
    return "\n".join(out)


def lint(rule: str, existing: list[dict]) -> list[str]:
    """Reject rules that overfit to the test set or bloat the prompt."""
    errors = []
    if len(rule.split()) > MAX_RULE_WORDS:
        errors.append(f"longer than {MAX_RULE_WORDS} words")
    if re.search(r"\b[SR]\d{2}\b|\bA\d{4}\b|\bP\d-\d{8}", rule):
        errors.append("mentions a scenario, appointment or slot id")
    if re.search(
        r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2} (January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\b",
        rule,
    ):
        errors.append("mentions a specific date")
    names = {n for p in Clinic.load().patients.values() for n in p["name"].split()}
    if any(re.search(rf"\b{re.escape(n)}\b", rule) for n in names):
        errors.append("mentions a patient name")
    norm = lambda s: re.sub(r"\W+", " ", s.casefold()).strip()
    if any(norm(rule) == norm(r["rule"]) for r in existing):
        errors.append("duplicates an existing rule")
    if len(existing) >= MAX_RULES:
        errors.append(f"learned_rules is full ({MAX_RULES}); consolidate before adding")
    return errors


def propose_rule(config: dict, by_check: dict[str, list[dict]], client: OpenAI, feedback: str = "") -> Proposal:
    prompt = _PROMPT.format(
        max_words=MAX_RULE_WORDS,
        core_prompt=config["core_prompt"],
        rules="\n".join(f"- {r['rule']}" for r in config.get("learned_rules") or []) or "(none)",
        failures=_render_failures(by_check),
        feedback=f"\nA PREVIOUS ATTEMPT WAS REJECTED: {feedback}\n" if feedback else "",
    )
    raw = (
        client.chat.completions.create(
            model=os.getenv("PROPOSER_MODEL", "gpt-4.1"),
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        .choices[0]
        .message.content
    )
    data = json.loads(raw)
    p = Proposal(
        rule=str(data.get("rule", "")).strip(),
        why=str(data.get("why", "")).strip(),
        fixes=[str(x) for x in data.get("fixes", [])],
        check=str(data.get("check", "")),
    )
    # The proposer may only claim scenarios it was shown failing.
    shown = {i["scenario"] for items in by_check.values() for i in items}
    p.fixes = [f for f in p.fixes if f in shown] or sorted(shown)
    p.lint_errors = lint(p.rule, config.get("learned_rules") or [])
    return p
