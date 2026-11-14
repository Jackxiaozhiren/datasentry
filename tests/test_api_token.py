"""D5-02 回归：`DATASENTRY_API_TOKEN` 设置时数据面需 `X-Datasentry-Token`。

- 未设置：行为零变化（本地单机默认，与 CLI 同信任边界）；
- 设置后：`/pii/*` 全量 + repairs 写端点无头/错头 → 401，正确头放行。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datasentry.api import create_app

_TOKEN = "test-api-token-xyz"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(project=tmp_path))


@pytest.fixture
def guarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATASENTRY_API_TOKEN", _TOKEN)
    return TestClient(create_app(project=tmp_path))


def _h(token: str | None) -> dict[str, str]:
    return {"X-Datasentry-Token": token} if token else {}


class TestApiToken:
    def test_unset_token_keeps_local_default(self, client: TestClient) -> None:
        """未设 token：pii/rollback 保持原状态码（503 缺 key / 404 无 run）。"""
        assert client.get("/pii/sessions").status_code == 503
        assert client.post("/repairs/rep_nope/rollback").status_code == 404

    def test_pii_requires_token_when_set(self, guarded: TestClient) -> None:
        assert guarded.get("/pii/sessions").status_code == 401
        assert guarded.get("/pii/sessions", headers=_h("wrong")).status_code == 401
        # 正确头放行（vault 未配 key → 503，证明已越过鉴权层）
        authed = guarded.get("/pii/sessions", headers=_h(_TOKEN))
        assert authed.status_code == 503

    def test_repairs_write_requires_token_when_set(self, guarded: TestClient) -> None:
        assert guarded.post("/repairs/rep_nope/rollback").status_code == 401
        authed = guarded.post("/repairs/rep_nope/rollback", headers=_h(_TOKEN))
        assert authed.status_code == 404

    def test_reads_stay_open(self, guarded: TestClient) -> None:
        """读端点（health/scans 列表）不受 token 影响。"""
        assert guarded.get("/health").status_code == 200
        assert guarded.get("/scans").status_code == 200


class TestWriteGuardMiddleware:
    """D5-10/D5-11: one gate covers every mutating route, so no new route can be left open."""

    def test_rest_writes_need_the_token_once_it_is_configured(
        self, guarded: TestClient, tmp_path: Path
    ) -> None:
        for method, url in (
            ("post", "/scans"),
            ("post", "/jobs"),
            ("post", "/ui/scans"),
            ("delete", "/jobs/nope"),
            ("patch", "/jobs/nope"),
        ):
            client_method = getattr(guarded, method)
            response = (
                client_method(url, json={"path": str(tmp_path / "a.csv")})
                if method != "delete"
                else client_method(url)
            )
            assert response.status_code == 401, f"{method.upper()} {url}"
            assert response.json()["ok"] is False

    def test_correct_header_satisfies_the_gate(self, guarded: TestClient, tmp_path: Path) -> None:
        csv = tmp_path / "ws" / "orders.csv"
        csv.parent.mkdir()
        csv.write_text("id,amount\n1,10.0\n", encoding="utf-8")
        response = guarded.post("/scans", json={"path": str(csv)}, headers=_h(_TOKEN))
        assert response.status_code == 201, response.text

    def test_reads_are_not_gated(self, guarded: TestClient) -> None:
        assert guarded.get("/health").status_code == 200
        assert guarded.get("/jobs").status_code == 200

    def test_cross_origin_form_post_is_refused_without_a_token(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Another site can point a browser at a local DataSentry; that is refused."""
        response = client.post(
            "/ui/scans",
            data={"path": str(tmp_path / "a.csv")},
            headers={"Origin": "http://evil.example"},
        )
        assert response.status_code == 401
        assert "cross-origin" in response.json()["detail"]

    def test_worker_rpc_keeps_its_own_credential(self, client: TestClient) -> None:
        """`/rpc/*` is the gate's only exemption and authenticates with the worker token instead."""
        assert client.post("/rpc/execute", json={}).status_code == 503


class TestWritePolicy:
    """The policy table, asserted where the peer address can be set for real."""

    @staticmethod
    def _request(
        monkeypatch: pytest.MonkeyPatch, *, path: str, peer: str, origin: str | None, token: str
    ) -> bool:
        from starlette.requests import Request

        from datasentry.api import _write_allowed

        monkeypatch.setenv("DATASENTRY_API_TOKEN", token)
        headers = [(b"host", b"127.0.0.1:8000")]
        if origin:
            headers.append((b"origin", origin.encode()))
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers,
            "client": (peer, 50000),
            "query_string": b"",
            "root_path": "",
            "http_version": "1.1",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
        }
        allowed, _ = _write_allowed(Request(scope))
        return allowed

    def test_ui_write_from_loopback_browser_is_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P17's answer: a form cannot send a header, so same-origin loopback is enough."""
        assert self._request(
            monkeypatch,
            path="/ui/scans",
            peer="127.0.0.1",
            origin="http://127.0.0.1:8000",
            token="t-1",
        )

    def test_ui_write_from_a_remote_peer_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert not self._request(
            monkeypatch, path="/ui/scans", peer="10.0.0.7", origin=None, token="t-1"
        )

    def test_rest_write_from_loopback_without_a_header_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The loopback exemption is scoped to the form-submitted UI face and nothing else."""
        assert not self._request(
            monkeypatch, path="/scans", peer="127.0.0.1", origin=None, token="t-1"
        )

    def test_127_0_0_2_counts_as_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Peer 127.0.0.2 is loopback too — the whole /8, not the string list D5-01 reports."""
        assert self._request(
            monkeypatch,
            path="/ui/scans",
            peer="127.0.0.2",
            origin="http://127.0.0.1:8000",
            token="t-1",
        )
