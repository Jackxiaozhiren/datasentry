"""Console entrypoint with a zero-configuration demo path.

Keeping demo dispatch here lets the existing CLI parser remain stable while making
`datasentry demo` a first-class onboarding command.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from datasentry.cli import main as cli_main
from datasentry.demo import run_demo


def _demo_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datasentry demo",
        description="Run an offline, deterministic DataSentry demo on synthetic dirty data.",
    )
    parser.add_argument("--rows", type=int, default=1000, help="number of synthetic rows")
    parser.add_argument("--out", type=Path, default=None, help="artifact output directory")
    parser.add_argument("--project", type=Path, default=None, help="workspace for demo metadata")
    parser.add_argument("--seed", type=int, default=42, help="reproducibility seed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "demo":
        ns = _demo_parser().parse_args(args[1:])
        return run_demo(rows=ns.rows, out=ns.out, project=ns.project, seed=ns.seed)
    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
