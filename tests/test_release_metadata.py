from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _compatible_upper_bound(version: str) -> str:
    major, minor, _patch = (int(part) for part in version.split(".", maxsplit=2))
    if major == 0:
        return f"0.{minor + 1}.0"
    return f"{major + 1}.0.0"


def test_datasentry_ai_requires_current_core_release_line() -> None:
    app = _load_toml(ROOT / "pyproject.toml")
    core = _load_toml(ROOT / "packages" / "core" / "pyproject.toml")

    app_project = app["project"]
    core_project = core["project"]
    assert isinstance(app_project, dict)
    assert isinstance(core_project, dict)

    core_version = core_project["version"]
    dependencies = app_project["dependencies"]
    assert isinstance(core_version, str)
    assert isinstance(dependencies, list)

    upper = _compatible_upper_bound(core_version)
    core_requirements = [
        dep for dep in dependencies if isinstance(dep, str) and dep.startswith("datasentry-core")
    ]
    assert core_requirements == [f"datasentry-core>={core_version},<{upper}"]


def test_release_versions_are_not_reused_from_broken_pair() -> None:
    app = _load_toml(ROOT / "pyproject.toml")
    core = _load_toml(ROOT / "packages" / "core" / "pyproject.toml")

    app_project = app["project"]
    core_project = core["project"]
    assert isinstance(app_project, dict)
    assert isinstance(core_project, dict)

    # PyPI datasentry-ai 1.0.0 can resolve to the older published
    # datasentry-core 0.7.0, which lacks APIs imported by the 1.0.0 app wheel.
    # Never reuse either version for the repaired release pair.
    assert app_project["version"] != "1.0.0"
    assert core_project["version"] != "0.7.0"
