"""D5-01/D5-02 回归（阶段 2 行 r2+r3）：非回环绑定的启动姿态。

`uvicorn.run` 全程打桩，测试不实绑任何端口；断言的是"能不能启动、启动时警告什么"。
"""

from __future__ import annotations

import logging

import pytest

from datasentry.api import INSECURE_BIND_OPT_IN, InsecureBindRefused, main, resolve_bind


@pytest.fixture()
def run_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)
    for var in ("DATASENTRY_API_TOKEN", INSECURE_BIND_OPT_IN, "DATASENTRY_HOST"):
        monkeypatch.delenv(var, raising=False)
    return calls


class TestResolveBind:
    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "127.0.0.2", "127.255.1.9", "::1", "::ffff:127.0.0.1", "localhost"]
    )
    def test_every_loopback_form_starts(self, host: str) -> None:
        """The old check was `host not in {"127.0.0.1", "localhost", "::1"}`, so 127.0.0.2 was
        reported as non-loopback and ::ffff:127.0.0.1 walked around it entirely."""
        assert resolve_bind(host, token=None, opted_in=False) is None

    @pytest.mark.parametrize("host", ["", "0.0.0.0", "::", "10.0.0.5", "example.com"])
    def test_non_loopback_with_nothing_configured_is_refused(self, host: str) -> None:
        with pytest.raises(InsecureBindRefused) as exc:
            resolve_bind(host, token=None, opted_in=False)
        message = str(exc.value)
        assert "DATASENTRY_API_TOKEN" in message
        assert INSECURE_BIND_OPT_IN in message

    def test_a_token_makes_a_wildcard_bind_advisory(self) -> None:
        warning = resolve_bind("0.0.0.0", token="t-1", opted_in=False)
        assert warning is not None and "X-Datasentry-Token" in warning

    def test_opting_in_without_a_token_starts_but_says_so(self) -> None:
        warning = resolve_bind("0.0.0.0", token=None, opted_in=True)
        assert warning is not None and "NO API token" in warning

    def test_loopback_never_warns_even_with_a_token(self) -> None:
        assert resolve_bind("127.0.0.1", token="t-1", opted_in=False) is None


class TestMainWiring:
    def test_default_bind_is_loopback_and_starts(self, run_calls: list[dict[str, object]]) -> None:
        main([])
        assert run_calls and run_calls[0]["host"] == "127.0.0.1"

    def test_wildcard_without_a_token_refuses_before_binding(
        self, run_calls: list[dict[str, object]]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--host", "0.0.0.0"])
        assert "refusing to bind" in str(exc.value)
        assert run_calls == []

    def test_wildcard_with_a_token_starts(self, run_calls, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_API_TOKEN", "t-1")
        main(["--host", "0.0.0.0"])
        assert run_calls[0]["host"] == "0.0.0.0"

    def test_wildcard_opted_in_starts(
        self, run_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(INSECURE_BIND_OPT_IN, "1")
        main(["--host", "0.0.0.0"])
        assert run_calls[0]["host"] == "0.0.0.0"

    def test_a_127_0_0_2_bind_is_not_broadcast_as_exposed(
        self, run_calls: list[dict[str, object]], caplog: pytest.LogCaptureFixture
    ) -> None:
        """G1.6 measured this being logged as "binding non-loopback host"; it is loopback, so
        the whole 127/8 must start silently instead of training operators to ignore warnings."""
        with caplog.at_level(logging.WARNING, logger="datasentry.api"):
            main(["--host", "127.0.0.2"])
        assert run_calls[0]["host"] == "127.0.0.2"
        assert [r.getMessage() for r in caplog.records] == []


class TestWorkerWiring:
    """复核 A-6：`datasentry worker` 跑的是整个应用，不能只按 `/rpc/execute` 的 token 判暴露面。

    `--token`/`DATASENTRY_WORKER_TOKEN` 只护 `/rpc/execute`；未设 `DATASENTRY_API_TOKEN` 时
    其余写端点对任何能路由到该端口的人开放（无 Origin 头的请求被当作同源，curl 正是如此）。
    """

    def test_worker_on_a_wildcard_without_an_api_token_refuses_to_start(
        self, run_calls: list[dict[str, object]], tmp_path: object
    ) -> None:
        from datasentry.cli import EXIT_CONFIG
        from datasentry.cli import main as cli_main

        code = cli_main(["--project", str(tmp_path), "worker", "--host", "0.0.0.0"])
        assert code == EXIT_CONFIG
        assert run_calls == [], "uvicorn.run was reached: the bind guard did not stop it"

    def test_a_worker_token_alone_does_not_buy_a_public_bind(
        self, run_calls: list[dict[str, object]], tmp_path: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """这条是 A-6 的核心：worker token 只覆盖 `/rpc/*`，不能当成对外暴露的许可。"""
        from datasentry.cli import EXIT_CONFIG
        from datasentry.cli import main as cli_main

        monkeypatch.setenv("DATASENTRY_WORKER_TOKEN", "worker-secret")
        code = cli_main(["--project", str(tmp_path), "worker", "--host", "0.0.0.0"])
        assert code == EXIT_CONFIG
        assert run_calls == []

    def test_worker_starts_on_a_wildcard_when_an_api_token_is_set(
        self,
        run_calls: list[dict[str, object]],
        tmp_path: object,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from datasentry.cli import main as cli_main

        monkeypatch.setenv("DATASENTRY_API_TOKEN", "api-secret")
        code = cli_main(["--project", str(tmp_path), "--format", "json", "worker", "--host", "*"])
        assert code == 0
        assert run_calls and run_calls[0]["host"] == "*"
        assert "X-Datasentry-Token" in capsys.readouterr().out

    def test_worker_default_bind_stays_loopback_and_says_nothing(
        self,
        run_calls: list[dict[str, object]],
        tmp_path: object,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from datasentry.cli import main as cli_main

        code = cli_main(["--project", str(tmp_path), "--format", "json", "worker"])
        assert code == 0
        assert run_calls and run_calls[0]["host"] == "127.0.0.1"
        assert "notice" not in capsys.readouterr().out
