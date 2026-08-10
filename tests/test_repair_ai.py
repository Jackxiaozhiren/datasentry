"""AI 修复候选（Step 44）：AIRepairService + client/CLI 集成。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry.client import DataSentry
from datasentry.repair_ai import AIRepairService
from datasentry_core.storage.store import MetadataStore


class _FakeProvider:
    provider_id = "fake"
    model = "fake-model"

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.last_request = None

    def complete(self, request):
        self.last_request = request
        return type(
            "R",
            (),
            {
                "text": self.payload,
                "input_tokens": 42,
                "output_tokens": 7,
                "cache_hit": False,
                "model": self.model,
                "latency_ms": 5,
            },
        )()


@pytest.fixture()
def repair_csv(tmp_path: Path) -> Path:
    rows = ["name,status,price"]
    for i in range(1, 21):
        rows.append(f" user{i} ,active,{10 + i}")
    p = tmp_path / "repair.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


@pytest.fixture()
def outlier_csv(tmp_path: Path) -> Path:
    rows = ["name,price"]
    for i in range(1, 21):
        rows.append(f"user{i},{10 + i}")
    rows.append("user_bad,9999")
    p = tmp_path / "outlier.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _issue_for(client: DataSentry, path: Path, detector_id: str):
    _, _, issues = client.scan_file(path)
    return next(i for i in issues if detector_id in i.detector_ids)


def _service(store: MetadataStore, provider) -> tuple[AIRepairService, MetadataStore]:
    return AIRepairService(store=store, provider=provider), store


def _store_for(tmp_path: Path) -> MetadataStore:
    return MetadataStore.for_workspace(tmp_path)


class TestService:
    def test_proposes_trim_candidate(self, tmp_path: Path, repair_csv: Path) -> None:
        provider = _FakeProvider(
            json.dumps(
                {"operation": "trim_whitespace", "parameters": {}, "rationale": "strip spaces"}
            )
        )
        service, store = _service(_store_for(tmp_path), provider)
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, repair_csv, "leading_or_trailing_whitespace")
            result = service.propose(issue.id, str(repair_csv))
            assert result.proposal is not None
            assert result.proposal.operation.value == "trim_whitespace"
            assert result.proposal.issue_type == "trim_whitespace"
            assert result.proposal.rationale == "strip spaces"
            assert result.proposal.estimated_rows_changed > 0
            stored = store.get_repair_proposal(result.proposal.proposal_id)
            assert stored is not None
            assert stored.issue_id == issue.id
        finally:
            client.close()

    def test_disallowed_operation_rejected(self, tmp_path: Path, repair_csv: Path) -> None:
        provider = _FakeProvider(
            json.dumps({"operation": "set_null", "parameters": {}, "rationale": "nuke"})
        )
        service, _ = _service(_store_for(tmp_path), provider)
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, repair_csv, "leading_or_trailing_whitespace")
            result = service.propose(issue.id, str(repair_csv))
            assert result.proposal is None
            assert result.rejected_reason is not None
            assert "not allowed" in result.rejected_reason
        finally:
            client.close()

    def test_clip_value_parameters_validate(self, tmp_path: Path, outlier_csv: Path) -> None:
        provider = _FakeProvider(
            json.dumps(
                {
                    "operation": "clip_value",
                    "parameters": {"lower": 5.0, "upper": 40.0},
                    "rationale": "clip outlier to range",
                }
            )
        )
        service, _ = _service(_store_for(tmp_path), provider)
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, outlier_csv, "iqr_outlier")
            result = service.propose(issue.id, str(outlier_csv))
            assert result.proposal is not None
            assert result.proposal.parameters == {"lower": 5.0, "upper": 40.0}
        finally:
            client.close()

    def test_bad_clip_parameters_rejected(self, tmp_path: Path, outlier_csv: Path) -> None:
        provider = _FakeProvider(
            json.dumps(
                {
                    "operation": "clip_value",
                    "parameters": {"lower": "NaN", "upper": 40},
                    "rationale": "bad",
                }
            )
        )
        service, _ = _service(_store_for(tmp_path), provider)
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, outlier_csv, "iqr_outlier")
            result = service.propose(issue.id, str(outlier_csv))
            assert result.proposal is None
            assert "invalid parameters" in (result.rejected_reason or "")
        finally:
            client.close()

    def test_non_json_output_schema_failed(self, tmp_path: Path, repair_csv: Path) -> None:
        service, _ = _service(_store_for(tmp_path), _FakeProvider("not json at all"))
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, repair_csv, "leading_or_trailing_whitespace")
            result = service.propose(issue.id, str(repair_csv))
            assert result.proposal is None
            assert "output schema failed" in (result.rejected_reason or "")
        finally:
            client.close()

    def test_no_ai_repairable_detector(self, tmp_path: Path) -> None:
        dup_csv = tmp_path / "dup.csv"
        dup_csv.write_text("id,name\n1,a\n2,a\n3,a\n", encoding="utf-8")
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, dup_csv, "uniqueness_violation")
            assert AIRepairService._allowed_operations(issue) == set()
            result = AIRepairService(store=client._store, provider=_FakeProvider("{}")).propose(
                issue.id, str(dup_csv)
            )
            assert result.proposal is None
            assert "no AI-repairable detector" in (result.rejected_reason or "")
        finally:
            client.close()

    def test_llm_error_audited(self, tmp_path: Path, repair_csv: Path) -> None:
        class _BoomProvider:
            provider_id = "boom"
            model = "boom-model"

            def complete(self, request):
                from datasentry_core.llm.provider import LLMError

                raise LLMError("timeout")

        service, store = _service(_store_for(tmp_path), _BoomProvider())
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, repair_csv, "leading_or_trailing_whitespace")
            result = service.propose(issue.id, str(repair_csv))
            assert result.llm_error == "timeout"
            invocations = store.list_llm_invocations()
            assert len(invocations) == 1
            assert invocations[0].status == "failed"
            assert invocations[0].task_type == "repair_candidate"
        finally:
            client.close()

    def test_not_configured(self, tmp_path: Path, repair_csv: Path) -> None:
        from datasentry.llm_providers import NullProvider
        from datasentry_core.llm.provider import LLMNotConfiguredError

        service, _ = _service(_store_for(tmp_path), NullProvider())
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, repair_csv, "leading_or_trailing_whitespace")
            with pytest.raises(LLMNotConfiguredError):
                service.propose(issue.id, str(repair_csv))
        finally:
            client.close()

    def test_cache_hit_second_call(self, tmp_path: Path, repair_csv: Path) -> None:
        provider = _FakeProvider(
            json.dumps({"operation": "trim_whitespace", "parameters": {}, "rationale": "strip"})
        )
        service, store = _service(_store_for(tmp_path), provider)
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, repair_csv, "leading_or_trailing_whitespace")
            first = service.propose(issue.id, str(repair_csv))
            second = service.propose(issue.id, str(repair_csv))
            assert first.proposal is not None and second.proposal is not None
            assert second.cache_hit is True
            invocations = store.list_llm_invocations()
            assert len(invocations) == 1
        finally:
            client.close()

    def test_unknown_issue(self, tmp_path: Path, repair_csv: Path) -> None:
        service, _ = _service(_store_for(tmp_path), _FakeProvider("{}"))
        with pytest.raises(KeyError):
            service.propose("missing", str(repair_csv))


class TestClientIntegration:
    def test_repair_propose_ai(self, tmp_path: Path, repair_csv: Path, monkeypatch) -> None:
        from datasentry import repair_ai

        monkeypatch.setattr(
            repair_ai,
            "create_provider",
            lambda: _FakeProvider(
                json.dumps({"operation": "trim_whitespace", "parameters": {}, "rationale": "strip"})
            ),
        )
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, repair_csv, "leading_or_trailing_whitespace")
            proposal = client.repair_propose_ai(issue.id, repair_csv)
            assert proposal is not None
            assert proposal.operation.value == "trim_whitespace"
        finally:
            client.close()


class TestVaultRoundtrip:
    """加密 → LLM 往返 → 还原（Step 48）：映射加密落库、回复还原、审计。"""

    @pytest.fixture()
    def pii_csv(self, tmp_path: Path) -> Path:
        rows = ["name,email"]
        for i in range(1, 11):
            rows.append(f" user{i} ,user{i}@example.com")
        p = tmp_path / "pii_repair.csv"
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return p

    def test_propose_encrypts_mapping_and_restores_rationale(
        self, tmp_path: Path, pii_csv: Path
    ) -> None:
        provider = _FakeProvider(
            json.dumps(
                {
                    "operation": "trim_whitespace",
                    "parameters": {},
                    "rationale": "trim rows for {{REDACTED:email:0}}",
                }
            )
        )
        store = _store_for(tmp_path)
        service = AIRepairService(store=store, provider=provider)
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, pii_csv, "leading_or_trailing_whitespace")
            result = service.propose(issue.id, str(pii_csv))
            assert result.proposal is not None
            assert "{{REDACTED" not in result.proposal.rationale
            assert result.proposal.rationale.startswith("trim rows for ")
            assert result.proposal.rationale.endswith("@example.com")
            assert store.count_pii_mappings() == 1
            row = store.get_pii_mapping(store.list_pii_mappings()[0]["session_id"])
            assert row is not None
            assert "example.com" not in row["ciphertext"]
            invocations = store.list_llm_invocations()
            assert invocations[0].pii_session_id is not None
            assert invocations[0].pii_session_id.startswith("pii_")
        finally:
            client.close()

    def test_restore_via_cli_text(self, tmp_path: Path, pii_csv: Path, capsys) -> None:
        provider = _FakeProvider(
            json.dumps(
                {
                    "operation": "trim_whitespace",
                    "parameters": {},
                    "rationale": "ok",
                }
            )
        )
        from datasentry.cli import main

        store = _store_for(tmp_path)
        service = AIRepairService(store=store, provider=provider)
        client = DataSentry(project=tmp_path)
        try:
            issue = _issue_for(client, pii_csv, "leading_or_trailing_whitespace")
            service.propose(issue.id, str(pii_csv))
            session_id = store.list_pii_mappings()[0]["session_id"]
        finally:
            client.close()
        code = main(
            [
                "--project",
                str(tmp_path),
                "--format",
                "json",
                "llm",
                "restore",
                session_id,
                "--text",
                "contact {{REDACTED:email:0}}",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "llm restore"
        assert payload["data"]["restored"].endswith("@example.com")
