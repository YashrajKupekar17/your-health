"""Measure the emergency gate on labelled messages: recall (missed emergencies) and over-escalation.

python -m evals.redflags   (no API key, no LLM; also enforced by tests/test_redflags.py)
"""

from __future__ import annotations

from pathlib import Path

import yaml

from yourhealth.safety import emergency_match

from .stats import fmt_interval

REDFLAGS_PATH = Path(__file__).resolve().parent / "redflags.yaml"


def evaluate(path: Path = REDFLAGS_PATH) -> dict:
    rows = yaml.safe_load(Path(path).read_text())
    for r in rows:
        r["fired"] = emergency_match(r["text"]) is not None
    gate = [r for r in rows if r["emergency"] and r.get("layer") == "gate"]
    prompt = [r for r in rows if r["emergency"] and r.get("layer") == "prompt"]
    routine = [r for r in rows if not r["emergency"]]
    return {
        "gate_recall": (sum(r["fired"] for r in gate), len(gate)),
        "paraphrase_caught": (sum(r["fired"] for r in prompt), len(prompt)),
        "over_escalation": (sum(r["fired"] for r in routine), len(routine)),
        "missed": [r["text"] for r in gate if not r["fired"]],
        "false_alarms": [r["text"] for r in routine if r["fired"]],
    }


def main() -> None:
    m = evaluate()
    print(f"Gate recall on explicit red flags (target 100%): {fmt_interval(*m['gate_recall'])}")
    print(f"Paraphrases caught by the gate (prompt layer's job): {fmt_interval(*m['paraphrase_caught'])}")
    print(f"Over-escalation on routine messages (soft budget):  {fmt_interval(*m['over_escalation'])}")
    for t in m["missed"]:
        print(f"  MISSED emergency: {t!r}")
    for t in m["false_alarms"]:
        print(f"  false alarm: {t!r}")


if __name__ == "__main__":
    main()
