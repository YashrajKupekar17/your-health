"""LLM judge for soft criteria. It REPORTS; it never decides pass/fail or whether a change ships.

It sees the transcript AND the tool log, and may answer "unknown". It also answers one
question about the simulator ("did the patient stick to its card?") so simulator mistakes
can be kept out of the improvement loop.
"""

from __future__ import annotations

import json

from openai import OpenAI

from .scenario import Scenario

_PROMPT = """You review a conversation between a clinic scheduling assistant and a simulated patient.

PATIENT CARD (what the simulated patient was told to do):
{card}

CONVERSATION, with the assistant's tool calls in [brackets]:
{conversation}

Answer each criterion about the ASSISTANT with "pass", "fail" or "unknown" (if the conversation does
not give enough evidence), plus a one-sentence reason quoting the conversation.
Also answer whether the SIMULATED PATIENT followed its card (did not invent facts, contradict its
goal or end early for no reason).

CRITERIA:
{criteria}

Reply as JSON: {{"criteria": [{{"criterion": str, "verdict": "pass"|"fail"|"unknown", "reason": str}}],
"patient_followed_card": bool, "patient_note": str}}"""


def _render(trace: dict) -> str:
    lines = [f"assistant: {trace['greeting']}"]
    for t in trace["turns"]:
        lines.append(f"patient: {t['patient']}")
        for e in t["tools"]:
            status = "ok" if e["result"].get("ok") else e["result"].get("error")
            lines.append(f"  [{e['tool']}({json.dumps(e['args'])}) -> {status}]")
        lines.append(f"assistant: {t['agent']}")
    return "\n".join(lines)


def judge_trial(sc: Scenario, trace: dict, client: OpenAI, model: str) -> dict:
    criteria = sc.judge or ["(no assistant criteria for this scenario; return an empty list)"]
    prompt = _PROMPT.format(
        card=json.dumps(sc.patient, default=str),
        conversation=_render(trace),
        criteria="\n".join(f"- {c}" for c in criteria),
    )
    try:
        raw = (
            client.chat.completions.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            .choices[0]
            .message.content
        )
        out = json.loads(raw)
        return {
            "criteria": out.get("criteria", []) if sc.judge else [],
            "patient_followed_card": bool(out.get("patient_followed_card", True)),
            "patient_note": out.get("patient_note", ""),
        }
    except Exception as e:  # noqa: BLE001 - the judge is advisory; its failure must not sink the run
        return {"criteria": [], "patient_followed_card": True, "patient_note": f"judge error: {e}"}
