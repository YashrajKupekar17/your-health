"""Terminal chat with the agent. `--debug` prints every tool call and the session state."""

from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

from .agent import Agent

DIM, CYAN, RESET = "\033[2m", "\033[36m", "\033[0m"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="show tool calls and session state")
    args = parser.parse_args()
    load_dotenv()

    agent = Agent()
    s = agent.session
    print(f"{s.clinic.name} scheduling assistant (simulated now: {s.clinic.now:%a %d %b %Y %H:%M}). Ctrl-D to quit.\n")
    print(f"{CYAN}agent>{RESET} Hi, this is {s.clinic.name}. How can I help you today?")
    while not s.ended:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        seen = len(s.tool_log)
        reply = agent.respond(text)
        if args.debug:
            for entry in s.tool_log[seen:]:
                print(f"{DIM}  [{entry['tool']}] {json.dumps(entry['args'])} -> {json.dumps(entry['result'])[:300]}{RESET}")
            pending = s.pending.summary if s.pending else None
            print(f"{DIM}  state: turn={s.turn} patient={s.patient_id} pending={pending} handoff={s.handoff}{RESET}")
        print(f"{CYAN}agent>{RESET} {reply}")
    if s.handoff:
        print(f"{DIM}[transferred to staff: {s.handoff}]{RESET}")


if __name__ == "__main__":
    main()
