"""python -m evals [--scenario S01 S05] [--trials 3] [--no-judge] [--label baseline]"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from yourhealth.agent import load_config
from yourhealth.logs import configure_logging
from yourhealth.settings import load_env

from .runner import run_eval
from .scenario import load_scenarios


def main() -> None:
    p = argparse.ArgumentParser(description="Run the scheduling-agent eval suite.")
    p.add_argument("--scenario", nargs="*", help="scenario ids (default: all)")
    p.add_argument("--split", choices=["train", "holdout"])
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--no-judge", action="store_true", help="skip the LLM judge (faster, no simulator-error labels)")
    p.add_argument("--label", default="run")
    p.add_argument("--config", type=Path, help="agent config to evaluate (default: settings.config_path)")
    args = p.parse_args()
    load_env()
    configure_logging(os.getenv("LOG_LEVEL", "WARNING"))

    scenarios = load_scenarios(ids=args.scenario, split=args.split)
    results = run_eval(load_config(args.config), scenarios, args.trials, args.label, use_judge=not args.no_judge)
    print(Path(results["dir"], "report.md").read_text())
    print(f"artifacts: {results['dir']}")


if __name__ == "__main__":
    main()
