"""Step 28 测试：规则引擎预运行 + NL→候选生成（14.3/14.4，38 章）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry.rules_ai import RuleProposalService
from datasentry_core.models.enums import RuleType, Severity
from datasentry_core.models.rules import Condition, Rule
from datasentry_core.rules.engine import run_preflight
from datasentry_core.storage.store import MetadataStore

# ---- 规则引擎预运行（14.3） --------------------------------------------------


@pytest.fixture()
def csv_file(tmp_path: Path) -> Path:
    p = tmp_path / "orders.csv"
    p.write_text(
        "id,price,status,category\n"
        "1,100,active,cat_01\n"
        "2,-5,active,cat_01\n"
        "3,300,pending,cat_02\n"
        "4,50,closed,cat_01\n"
        "5,200,active,cat_03\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def handle(csv_file: Path):
    from datasentry_core.connectors import DataSourceSpec, default_registry

    handle = default_registry().open(
        DataSourceSpec(source_type="csv", path=csv_file, options={"dataset_id": "t"})
    )
    yield handle
    handle.close()


def _rule(**overrides) -> Rule:
    base = dict(
        id="rule_t1",
        type=RuleType.VALUE_RANGE,
        severity=Severity.MEDIUM,
        description="price must be positive",
        when=Condition(column="price", operator="gt", value=0),
        columns=["price"],
        source="user",
    )
    base.update(overrides)
    return Rule(**base)


def test_preflight_value_range(handle) -> None:
    report = run_preflight(_rule(), handle)
    assert report.valid is True
    assert report.schema_valid is True
    assert report.sample_run is not None
    assert report.sample_run.rows_tested == 5
    assert report.sample_run.failures == 1
    assert report.sample_run.failure_ratio == pytest.approx(0.2)
    assert len(report.sample_run.example_rows) == 1


def test_preflight_between_and_in(handle) -> None:
    between = run_preflight(
        _rule(when=Condition(column="price", operator="between", value=[50, 200])), handle
    )
    assert between.valid is True
    assert between.sample_run is not None
    assert between.sample_run.failures == 2  # -5, 300

    in_rule = run_preflight(
        _rule(
            id="rule_t2",
            type=RuleType.ALLOWED_VALUES,
            when=Condition(
                column="status", operator="not_in", value=["active", "pending", "closed"]
            ),
        ),
        handle,
    )
    assert in_rule.valid is True
    assert in_rule.sample_run is not None
    assert in_rule.sample_run.failures == 5  # 期望不在列表：5 行全在列表内→全违规


def test_preflight_missing_column(handle) -> None:
    report = run_preflight(_rule(when=Condition(column="nope", operator="gt", value=0)), handle)
    assert report.valid is False
    assert report.schema_valid is False
    assert report.sample_run is None


def test_preflight_dangerous_flag(handle) -> None:
    report = run_preflight(
        _rule(when=Condition(column="category", operator="not_equals", value="cat_01")), handle
    )
    assert report.dangerous is True  # 3/5 行违反 > 50%
    assert report.sample_run is not None
    assert report.sample_run.failures == 3


def test_preflight_null_operator(handle) -> None:
    p = handle.source_path
    assert p is not None
    # 无 NULL 数据：构造含空值的文件
    from datasentry_core.connectors import DataSourceSpec, default_registry

    empty = p.parent / "nulls.csv"
    empty.write_text("a,b\n1,x\n,\n2,\n", encoding="utf-8")
    h = default_registry().open(
        DataSourceSpec(source_type="csv", path=empty, options={"dataset_id": "t2"})
    )
    try:
        report = run_preflight(
            _rule(
                id="rule_t3",
                when=Condition(column="b", operator="is_null"),
                columns=["b"],
            ),
            h,
        )
        assert report.valid is True
        assert report.sample_run is not None
        assert report.sample_run.failures == 1  # 期望 b 为 NULL：仅 b='x' 违规
    finally:
        h.close()


# ---- NL → 规则候选（14.4） ---------------------------------------------------


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


def _service(tmp_path: Path, provider: _FakeProvider) -> tuple[RuleProposalService, MetadataStore]:
    store = MetadataStore(tmp_path / "m.db")
    return RuleProposalService(store=store, provider=provider), store


def _payload_json(rules: list[dict]) -> str:
    return json.dumps({"rules": rules})


def test_propose_valid_candidates(tmp_path: Path, csv_file: Path) -> None:
    provider = _FakeProvider(
        _payload_json(
            [
                {
                    "type": "value_range",
                    "severity": "high",
                    "description": "price must be positive",
                    "when": {"column": "price", "operator": "gt", "value": 0},
                    "columns": ["price"],
                    "confidence": 0.95,
                    "paraphrase": "negative prices are invalid",
                    "notes": ["n1"],
                }
            ]
        )
    )
    service, store = _service(tmp_path, provider)
    result = service.propose("prices must be positive", str(csv_file))
    assert result.llm_error is None
    assert len(result.rules) == 1
    item = result.rules[0]
    assert item.candidate is not None
    rule = item.candidate.rule
    assert rule.type == RuleType.VALUE_RANGE
    assert rule.severity == Severity.HIGH
    assert rule.source == "llm_candidate"
    assert rule.enabled is False  # 候选未批准
    assert item.preflight is not None and item.preflight.valid is True
    assert item.preflight.sample_run is not None
    assert item.preflight.sample_run.failures == 1
    # 候选已落库待批准
    stored = store.list_rules()
    assert len(stored) == 1
    assert stored[0].enabled is False
    assert stored[0].source == "llm_candidate"


def test_propose_masks_pii_before_llm(tmp_path: Path, csv_file: Path) -> None:
    pii_file = csv_file.parent / "pii.csv"
    pii_file.write_text(
        "email,price\nalice@example.com,100\nbob@corp.io,-5\n",
        encoding="utf-8",
    )
    provider = _FakeProvider(_payload_json([]))
    service, _ = _service(tmp_path, provider)
    result = service.propose("rule on emails", str(pii_file))
    assert result.masked_sample_count == 2
    prompt = provider.last_request.prompt
    assert "alice@example.com" not in prompt
    assert "bob@corp.io" not in prompt
    assert "{{REDACTED:email:0}}" in prompt


def test_propose_schema_failure_reports(tmp_path: Path, csv_file: Path) -> None:
    provider = _FakeProvider("not json at all")
    service, _ = _service(tmp_path, provider)
    result = service.propose("any rule", str(csv_file))
    assert result.rules == [] or result.rules[0].rejected_reason is not None
    assert len(result.rules) == 1
    assert "output schema failed" in result.rules[0].rejected_reason


def test_propose_llm_error_audited(tmp_path: Path, csv_file: Path) -> None:
    class _BoomProvider:
        provider_id = "boom"
        model = "boom-model"

        def complete(self, request):
            from datasentry_core.llm.provider import LLMError

            raise LLMError("timeout")

    service, store = _service(tmp_path, _BoomProvider())
    result = service.propose("any rule", str(csv_file))
    assert result.llm_error == "timeout"
    invocations = store.list_llm_invocations()
    assert len(invocations) == 1
    assert invocations[0].status == "failed"
    assert invocations[0].error_message == "timeout"


def test_propose_not_configured(tmp_path: Path, csv_file: Path) -> None:
    from datasentry.llm_providers import NullProvider
    from datasentry_core.llm.provider import LLMNotConfiguredError

    service, _ = _service(tmp_path, NullProvider())
    with pytest.raises(LLMNotConfiguredError):
        service.propose("any rule", str(csv_file))


def test_cache_hit_skips_provider(tmp_path: Path, csv_file: Path) -> None:
    payload = _payload_json(
        [
            {
                "type": "not_null",
                "severity": "medium",
                "description": "price not null",
                "when": {"column": "price", "operator": "is_null"},
                "columns": ["price"],
                "confidence": 0.9,
                "paraphrase": "p",
                "notes": [],
            }
        ]
    )
    provider = _FakeProvider(payload)
    service, store = _service(tmp_path, provider)
    service.propose("price not null", str(csv_file))
    first_hash = provider.last_request.prompt
    result2 = service.propose("price not null", str(csv_file))
    assert result2.cache_hit is True
    assert provider.last_request.prompt == first_hash  # 同 prompt 缓存命中
    invocations = store.list_llm_invocations()
    assert len(invocations) == 1  # 仅首次调用有审计


def test_approve_activates_candidate(tmp_path: Path, csv_file: Path) -> None:
    provider = _FakeProvider(
        _payload_json(
            [
                {
                    "type": "not_null",
                    "severity": "low",
                    "description": "id not null",
                    "when": {"column": "id", "operator": "is_null"},
                    "columns": ["id"],
                    "confidence": 0.8,
                    "paraphrase": "p",
                    "notes": [],
                }
            ]
        )
    )
    service, store = _service(tmp_path, provider)
    result = service.propose("id must not be null", str(csv_file))
    rule_id = result.rules[0].candidate.rule.id
    approved = service.approve(rule_id)
    assert approved is not None
    assert approved.enabled is True
    stored = store.list_rules()
    assert stored[0].enabled is True
    # 批准不存在的规则 → None
    assert service.approve("rule_missing") is None


def test_invalid_rule_shape_rejected(tmp_path: Path, csv_file: Path) -> None:
    provider = _FakeProvider(
        _payload_json(
            [
                {
                    "type": "not_a_type",  # 不在 RuleType 白名单
                    "severity": "low",
                    "description": "x",
                    "when": {"column": "id", "operator": "bogus_op"},
                    "columns": ["id"],
                    "confidence": 0.5,
                    "paraphrase": "p",
                    "notes": [],
                }
            ]
        )
    )
    service, _ = _service(tmp_path, provider)
    result = service.propose("anything", str(csv_file))
    assert len(result.rules) == 1
    assert result.rules[0].candidate is None
    assert result.rules[0].rejected_reason is not None


# ---- Ollama 真实接入 E2E（Step 29，ADR-029） --------------------------------


def test_ollama_propose_approve_end_to_end(tmp_path: Path, csv_file: Path) -> None:
    """真实 OllamaProvider 走本地 /api/generate：propose→预运行→候选→approve→审计。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import httpx

    from datasentry.llm_providers import LLMConfig, OllamaProvider

    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            self.rfile.read(length)
            hits.append(self.path)
            inner = {
                "rules": [
                    {
                        "type": "value_range",
                        "severity": "high",
                        "description": "price must be positive",
                        "when": {"column": "price", "operator": "gt", "value": 0},
                        "columns": ["price"],
                        "confidence": 0.9,
                        "paraphrase": "no negative prices",
                        "notes": [],
                    }
                ]
            }
            data = json.dumps(
                {"model": "llama3", "response": json.dumps(inner), "done": True}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:  # 静默测试日志
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        provider = OllamaProvider(
            LLMConfig(
                provider="ollama",
                model="llama3",
                base_url=f"http://127.0.0.1:{port}",
                max_retries=1,
            ),
            transport=httpx.HTTPTransport(),  # 真实 TCP（非 MockTransport）
        )
        store = MetadataStore(tmp_path / "m.db")
        service = RuleProposalService(store=store, provider=provider)
        result = service.propose("prices must be positive", str(csv_file))
        assert len(result.rules) == 1
        assert result.llm_error is None
        item = result.rules[0]
        assert item.candidate is not None
        assert item.preflight.valid is True
        assert item.preflight.sample_run is not None
        assert item.preflight.sample_run.failures == 1  # price=-5 一行
        assert hits == ["/api/generate"]

        approved = service.approve(item.candidate.rule.id)
        assert approved is not None and approved.enabled is True
        stored = store.list_rules()
        assert [r.id for r in stored if r.enabled] == [approved.id]

        # cache：同描述二次 propose 不再打服务器
        second = service.propose("prices must be positive", str(csv_file))
        assert second.cache_hit is True
        assert hits == ["/api/generate"]
        invocations = store.list_llm_invocations()
        assert len(invocations) == 1
        assert invocations[0].provider_id == "ollama"
    finally:
        server.shutdown()
