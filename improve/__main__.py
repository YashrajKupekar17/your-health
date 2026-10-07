"""python -m improve [--yes] [--baseline runs/eval/<id>]  |  python -m improve rollback N  |  python -m improve --yes ablate R1"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from yourhealth.logs import configure_logging
from yourhealth.settings import load_env

from .loop import ablate, rollback, run_cycle


def main() -> None:
    p = argparse.ArgumentParser(description="Run one improvement cycle on the scheduling agent.")
    sub = p.add_subparsers(dest="cmd")
    rb = sub.add_parser("rollback", help="restore an archived config version")
    rb.add_argument("version", type=int)
    ab = sub.add_parser("ablate", help="run the suite without one learned rule; retire it if nothing regresses")
    ab.add_argument("rule_id")
    p.add_argument("--yes", action="store_true", help="apply automatically if the gate passes (skip the human prompt)")
    p.add_argument("--baseline", type=Path, help="reuse an existing eval run as the baseline")
    p.add_argument("--attempts", type=int, default=2)
    args = p.parse_args()
    load_env()
    configure_logging(os.getenv("LOG_LEVEL", "WARNING"))

    if args.cmd == "rollback":
        rollback(args.version)
    elif args.cmd == "ablate":
        ablate(args.rule_id, yes=args.yes)
    else:
        run_cycle(yes=args.yes, baseline_dir=args.baseline, max_attempts=args.attempts)


if __name__ == "__main__":
    main()
