from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER_NAME = "io.github.jackxiaozhiren/datasentry"


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


def test_runtime_versions_match_distribution_metadata() -> None:
    from datasentry import __version__ as app_runtime_version
    from datasentry_core import __version__ as core_runtime_version

    app = _load_toml(ROOT / "pyproject.toml")
    core = _load_toml(ROOT / "packages" / "core" / "pyproject.toml")
    app_project = app["project"]
    core_project = core["project"]
    assert isinstance(app_project, dict)
    assert isinstance(core_project, dict)

    assert app_runtime_version == app_project["version"]
    assert core_runtime_version == core_project["version"]


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


def test_mcp_registry_metadata_matches_app_release() -> None:
    app = _load_toml(ROOT / "pyproject.toml")
    app_project = app["project"]
    assert isinstance(app_project, dict)
    app_version = app_project["version"]
    assert isinstance(app_version, str)

    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert server["name"] == MCP_SERVER_NAME
    assert server["version"] == app_version

    packages = server["packages"]
    assert isinstance(packages, list)
    assert len(packages) == 1
    package = packages[0]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == "datasentry-ai"
    assert package["version"] == app_version
    assert package["runtimeHint"] == "uvx"
    assert package["runtimeArguments"] == [
        {"type": "named", "name": "--from", "value": f"datasentry-ai=={app_version}"}
    ]
    assert package["transport"] == {"type": "stdio"}
    assert package["packageArguments"] == [
        {"type": "positional", "value": "datasentry"},
        {"type": "positional", "value": "mcp"},
    ]

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<!-- mcp-name: {MCP_SERVER_NAME} -->" in readme
