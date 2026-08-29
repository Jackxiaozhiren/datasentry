"""Backward-compatible repository demo wrapper.

For installed users, prefer the product onboarding command:

    datasentry demo

Contributors can still run this file with:

    uv run python examples/demo/demo.py --rows 5000
"""

from datasentry.demo import main


if __name__ == "__main__":
    raise SystemExit(main())
