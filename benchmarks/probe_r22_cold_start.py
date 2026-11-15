"""R-22 cold-start probe: clean venv -> first report, on the official PyPI index.

One arm is one brand-new venv, ``pip install datasentry-ai`` with ``--no-cache-dir``
against https://pypi.org/simple, then ``datasentry demo`` in an isolated cwd. That is
the README's first path. A JSON line is appended per arm so the reading survives
instead of being re-remembered, and the ceiling is derived from those lines later.

Nothing here touches the repository worktree: venvs, demo outputs and logs live in
the scratch directory passed via ``--workdir``.

Usage::

    python3 benchmarks/probe_r22_cold_start.py --arms 3 \
        --out benchmarks/r22_cold_start_probe.jsonl \
        --workdir "$HOME/ds-probe-2026-10-15/r22-work"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OFFICIAL_INDEX = "https://pypi.org/simple/"
PACKAGE = "datasentry-ai"
#: `datasentry demo` 把产物写进 `tempfile.mkdtemp()`，**不是** cwd（实测）。所以
#: 「第一份报告」只能从它自己打印的 `HTML report: <path>` 行取，不能在 cwd 里 glob。
_REPORT_LINE = re.compile(r"^HTML report: (.+)$", re.MULTILINE)
_ARTIFACTS_LINE = re.compile(r"^Artifacts: (.+)$", re.MULTILINE)


def _network() -> dict[str, Any]:
    """Read the network condition at probe time, not from memory."""
    url = f"{OFFICIAL_INDEX}{PACKAGE}/"
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            code: Any = resp.status
            resp.read(4096)
    except Exception as exc:
        code = f"ERROR {type(exc).__name__}: {exc}"[:200]
    return {
        "index_url": OFFICIAL_INDEX,
        "simple_page_http": code,
        "simple_page_secs": round(time.perf_counter() - started, 4),
        "pip_index_url_env": os.environ.get("PIP_INDEX_URL"),
        "pip_extra_index_url_env": os.environ.get("PIP_EXTRA_INDEX_URL"),
        "mirror_used": False,
    }


def _timed(cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[float, int, str, str]:
    """(secs, rc, stdout, stderr) —— 两股流分开留，否则 stderr 的告警会把 stdout 尾部的
    `HTML report:` 行挤出截断窗口（第一版就踩了这个：证据里只剩 DuckDB 告警）。"""
    started = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=3600)
        rc, out, err = proc.returncode, proc.stdout[-8000:], proc.stderr[-2000:]
    except subprocess.TimeoutExpired:
        rc, out, err = -9, "", "TIMEOUT after 3600s"
    return round(time.perf_counter() - started, 3), rc, out, err


def _arm(index: int, workdir: Path, python: str, python_version: str) -> dict[str, Any]:
    venv = workdir / f"venv-{index}"
    run_dir = workdir / f"run-{index}"
    tmp_dir = workdir / f"tmp-{index}"
    if venv.exists():
        shutil.rmtree(venv)
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # A clean environment must not inherit the dev repo or a configured mirror.
    env = os.environ.copy()
    env.pop("PIP_INDEX_URL", None)
    env.pop("PIP_EXTRA_INDEX_URL", None)
    env.pop("PYTHONPATH", None)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    # 演示产物落在哪由 TMPDIR 决定：指到 scratch，既不写系统临时目录也不写仓库。
    env["TMPDIR"] = str(tmp_dir)

    venv_secs, venv_rc, _, venv_err = _timed([python, "-m", "venv", str(venv)], workdir, env)
    record: dict[str, Any] = {
        "arm": index,
        "utc": datetime.now(UTC).isoformat(),
        "python_version": python_version,
        "network": _network(),
        "venv_secs": venv_secs,
        "venv_rc": venv_rc,
    }
    if venv_rc != 0:
        record["error"] = venv_err
        return record

    vpy = str(venv / "bin" / "python")
    install_secs, install_rc, _, install_err = _timed(
        [
            vpy,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--index-url",
            OFFICIAL_INDEX,
            "--disable-pip-version-check",
            PACKAGE,
        ],
        run_dir,
        env,
    )
    record["install_secs"] = install_secs
    record["install_rc"] = install_rc
    if install_rc != 0:
        record["error"] = install_err
        return record

    _, _, version_out, _ = _timed(
        [vpy, "-c", "import importlib.metadata as m; print(m.version('datasentry-ai'))"],
        run_dir,
        env,
    )
    record["package_version"] = version_out.strip().splitlines()[-1] if version_out.strip() else ""

    demo = venv / "bin" / "datasentry"
    demo_secs, demo_rc, demo_out, demo_err = _timed([str(demo), "demo"], run_dir, env)
    record["demo_secs"] = demo_secs
    record["demo_rc"] = demo_rc
    artifacts = _ARTIFACTS_LINE.search(demo_out)
    report = _REPORT_LINE.search(demo_out)
    record["artifacts_dir"] = artifacts.group(1).strip() if artifacts else None
    record["report_path"] = report.group(1).strip() if report else None
    record["report_bytes"] = (
        Path(record["report_path"]).stat().st_size
        if report and Path(record["report_path"]).is_file()
        else None
    )
    # 「看见第一份报告」的判据是文件真的存在且非空，不是命令退出 0。
    record["report_written"] = bool(record["report_bytes"])
    record["demo_stdout_tail"] = demo_out[-1500:]
    record["demo_stderr_tail"] = demo_err[-600:]
    record["total_secs"] = round(venv_secs + install_secs + demo_secs, 3)
    return record


# pythonsecurity:S8707 models CLI arguments as attacker-controlled HTTP input. This
# probe is a local benchmark: --out and --workdir are typed by the operator, so that
# taint source does not exist. Paths are normalised before use, and every filesystem
# sink below carries a NOSONAR marker recording that decision.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()

    python = shutil.which("python3")
    if python is None:
        print("python3 not found", file=sys.stderr)
        return 2
    workdir = args.workdir.expanduser().resolve()
    out = args.out.expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)  # NOSONAR S8707: local CLI path
    out.parent.mkdir(parents=True, exist_ok=True)  # NOSONAR S8707: local CLI path
    _, _, version_out, _ = _timed([python, "--version"], workdir, os.environ.copy())
    python_version = (version_out.strip() or sys.version).splitlines()[-1]
    for i in range(1, args.arms + 1):
        reading = _arm(i, workdir, python, python_version)
        with out.open("a", encoding="utf-8") as handle:  # NOSONAR S8707: local CLI path
            handle.write(json.dumps(reading, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "arm": i,
                    **{
                        k: reading.get(k)
                        for k in (
                            "install_secs",
                            "demo_secs",
                            "total_secs",
                            "install_rc",
                            "demo_rc",
                        )
                    },
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
