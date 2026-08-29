"""Tests for the public console entrypoint and zero-config demo dispatch."""

from __future__ import annotations

from pathlib import Path

from datasentry import entrypoint


def test_demo_dispatches_without_touching_main_cli(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_demo(
        *,
        rows: int,
        out: Path | None,
        project: str | Path | None,
        seed: int,
    ) -> int:
        seen.update(rows=rows, out=out, project=project, seed=seed)
        return 17

    monkeypatch.setattr(entrypoint, "run_demo", fake_demo)
    out = tmp_path / "artifacts"
    project = tmp_path / "workspace"

    code = entrypoint.main(
        [
            "demo",
            "--rows",
            "25",
            "--out",
            str(out),
            "--project",
            str(project),
            "--seed",
            "7",
        ]
    )

    assert code == 17
    assert seen == {"rows": 25, "out": out, "project": project, "seed": 7}


def test_non_demo_commands_delegate_to_existing_cli(monkeypatch) -> None:
    seen: list[str] = []

    def fake_cli(argv: list[str]) -> int:
        seen.extend(argv)
        return 9

    monkeypatch.setattr(entrypoint, "cli_main", fake_cli)

    assert entrypoint.main(["scan", "orders.csv"]) == 9
    assert seen == ["scan", "orders.csv"]
