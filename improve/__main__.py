"""python -m improve [--yes] [--baseline runs/eval/<id>]   |   python -m improve rollback N"""

from __future__ import annotations

import argparse
from pathlib import Path

from yourhealth.settings import load_env

from .loop import rollback, run_cycle


def main() -> None:
    p = argparse.ArgumentParser(description="Run one improvement cycle on the scheduling agent.")
    sub = p.add_subparsers(dest="cmd")
    rb = sub.add_parser("rollback", help="restore an archived config version")
    rb.add_argument("version", type=int)
    p.add_argument("--yes", action="store_true", help="apply automatically if the gate passes (skip the human prompt)")
    p.add_argument("--baseline", type=Path, help="reuse an existing eval run as the baseline")
    p.add_argument("--attempts", type=int, default=2)
    args = p.parse_args()
    load_env()

    if args.cmd == "rollback":
        rollback(args.version)
    else:
        run_cycle(yes=args.yes, baseline_dir=args.baseline, max_attempts=args.attempts)


if __name__ == "__main__":
    main()
