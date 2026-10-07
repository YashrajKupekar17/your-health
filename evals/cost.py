"""Cost and latency per eval run, with the agent kept separate from the simulator and judge.

A prompt change that fixes a scenario but doubles cost or latency is a regression too, so the
report shows these next to pass rates. Prices are list prices per 1M tokens (input, output);
update them when the provider changes them. Unknown models report tokens without a $ estimate.
"""

from __future__ import annotations

from statistics import quantiles

PRICES_PER_M = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


def tokens_of(response) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return (0, 0)
    return (getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)


def dollars(model: str, tokens: tuple[int, int]) -> float | None:
    price = PRICES_PER_M.get(model)
    if price is None:
        return None
    return round((tokens[0] * price[0] + tokens[1] * price[1]) / 1e6, 6)


def _pct(values: list[float], q: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0])
    return round(quantiles(values, n=100, method="inclusive")[q - 1])


def summarize(usages: list[dict], models: dict) -> dict:
    """`usages`: one per trial with agent/sim/judge token pairs, turn latencies and LLM call counts."""
    out = {}
    for role in ("agent", "sim", "judge"):
        total = (sum(u[role][0] for u in usages), sum(u[role][1] for u in usages))
        out[role] = {
            "model": models[role],
            "tokens_in": total[0],
            "tokens_out": total[1],
            "usd": dollars(models[role], total),
        }
    turn_ms = [ms for u in usages for ms in u["turn_ms"]]
    turns = len(turn_ms)
    agent_usd = out["agent"]["usd"]
    out["agent"] |= {
        "usd_per_conversation": round(agent_usd / len(usages), 6) if agent_usd is not None and usages else None,
        "turn_ms_p50": _pct(turn_ms, 50),
        "turn_ms_p95": _pct(turn_ms, 95),
        "llm_calls_per_turn": round(sum(u["llm_calls"] for u in usages) / turns, 2) if turns else None,
    }
    return out


def render(cost: dict) -> list[str]:
    a = cost["agent"]
    usd = lambda v: "n/a" if v is None else f"${v:.4f}"
    return [
        "## Cost and latency",
        "",
        f"- **Agent** (`{a['model']}`): {a['tokens_in']:,} in / {a['tokens_out']:,} out tokens, {usd(a['usd'])} "
        f"total, {usd(a['usd_per_conversation'])} per conversation; turn latency p50 {a['turn_ms_p50']} ms, "
        f"p95 {a['turn_ms_p95']} ms; {a['llm_calls_per_turn']} LLM calls per turn",
        f"- **Simulator** (`{cost['sim']['model']}`): {usd(cost['sim']['usd'])} · "
        f"**Judge** (`{cost['judge']['model']}`): {usd(cost['judge']['usd'])} (eval overhead, not product cost)",
    ]
