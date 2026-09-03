#!/usr/bin/env python3
"""Fake teacup-agent CLI, for hermetic tests of sandbox.py / external_cli.py.

Mimics just enough of teacup-agent's real `--json` contract (that repo's
docs/integration.md) to exercise env allowlisting, timeout handling and
JSON-result parsing — without depending on a real teacup-agent checkout.
Two test-only flags (`--sleep`, `--echo-env`) exist purely to make sandbox
behavior observable; the real CLI has neither.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("task")
    p.add_argument("--json", action="store_true")
    p.add_argument("--budget", type=float, default=0.05)
    p.add_argument("--deadline", type=float, default=600.0)
    p.add_argument("--run-dir", default=None)
    p.add_argument("--memory", default=None)
    p.add_argument("--live", action="store_true")
    p.add_argument("--sleep", type=float, default=0.0, help="test-only: simulate a hung run")
    p.add_argument("--echo-env", default=None, help="test-only: print one env var to stderr")
    args = p.parse_args()

    if args.echo_env:
        print(f"ENV {args.echo_env}={os.environ.get(args.echo_env, '')}", file=sys.stderr)

    if args.sleep:
        time.sleep(args.sleep)

    print(
        json.dumps(
            {
                "status": "done",
                "answer": f"echo: {args.task}",
                "remaining_budget": max(0.0, args.budget - 0.01),
                "exit_code": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
