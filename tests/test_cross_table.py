"""Step 40：跨表外键完整性检测（契约 references → foreign_key_violation）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from datasentry_core.connectors.registry import default_registry
from datasentry_core.connectors.spec import DataSourceSpec
from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.cross_table import ForeignKeyViolationDetector
from datasentry_core.models.contract import Contract, TableReference
from datasentry_core.models.enums import QualityDimension
from datasentry_core.models.scan import ScanConfig

DETECTOR = ForeignKeyViolationDetector()


def _write_csv(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)


@pytest.fixture()
def orders_main(tmp_path: Path) -> Path:
    path = tmp_path / "orders.csv"
    _write_csv(
        path,
        ["order_id", "customer_id", "amount"],
        [
            [1, 10, 100.0],
            [2, 11, 200.0],
            [3, 99, 300.0],
            [4, None, 400.0],
            [5, 10, 500.0],
        ],
    )
    return path


@pytest.fixture()
def customers_ref(tmp_path: Path) -> Path:
    path = tmp_path / "customers.csv"
    _write_csv(
        path,
        ["id", "name"],
        [
            [10, "alice"],
            [11, "bob"],
        ],
    )
    return path


def _make_context(
    main_path: Path,
    references: list[TableReference],
) -> DetectionContext:
    spec = DataSourceSpec(source_type="csv", path=main_path, options={})
    handle = default_registry().open(spec)
    return DetectionContext(
        dataset_id="orders",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
        config=ScanConfig(),
        references=references,
    )


def _close(context: DetectionContext) -> None:
    context.handle.close()


def test_no_references_not_supported(orders_main: Path) -> None:
    context = _make_context(orders_main, [])
    try:
        assert not DETECTOR.supports(context)
    finally:
        _close(context)


def test_orphan_rows_detected(orders_main: Path, customers_ref: Path) -> None:
    ref = TableReference(
        name="customers",
        path=str(customers_ref),
        columns={"customer_id": "id"},
    )
    context = _make_context(orders_main, [ref])
    try:
        candidates = DETECTOR.detect(context)
        assert len(candidates) == 1
        issue = candidates[0]
        assert issue.issue_type == "foreign_key_violation"
        assert issue.columns == ["customer_id"]
        assert issue.affected_count == 1
        assert issue.suggested_severity == "high"
        data = issue.evidence[0].data
        assert data["reference"] == "customers"
        assert data["orphan_count"] == 1
        assert data["orphan_ratio"] == pytest.approx(1 / 4)
    finally:
        _close(context)


def test_all_match_no_issue(orders_main: Path, customers_ref: Path) -> None:
    path = orders_main.with_name("orders_clean.csv")
    _write_csv(
        path,
        ["order_id", "customer_id", "amount"],
        [[1, 10, 100.0], [2, 11, 200.0], [3, 10, 300.0]],
    )
    ref = TableReference(
        name="customers",
        path=str(customers_ref),
        columns={"customer_id": "id"},
    )
    context = _make_context(path, [ref])
    try:
        assert DETECTOR.detect(context) == []
    finally:
        _close(context)


def test_unknown_main_column_skipped(orders_main: Path, customers_ref: Path) -> None:
    ref = TableReference(
        name="customers",
        path=str(customers_ref),
        columns={"missing_col": "id"},
    )
    context = _make_context(orders_main, [ref])
    try:
        assert DETECTOR.detect(context) == []
    finally:
        _close(context)


def test_duckdb_reference(tmp_path: Path, orders_main: Path) -> None:
    import duckdb

    db_path = tmp_path / "ref.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE customers AS SELECT * FROM (VALUES (10, 'alice'), (11, 'bob')) t(id, name)"
    )
    con.close()

    ref = TableReference(
        name="customers",
        path=str(db_path),
        table="customers",
        columns={"customer_id": "id"},
    )
    context = _make_context(orders_main, [ref])
    try:
        candidates = DETECTOR.detect(context)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1
    finally:
        _close(context)


def test_multiple_references(tmp_path: Path, orders_main: Path, customers_ref: Path) -> None:
    regions = tmp_path / "regions.csv"
    _write_csv(
        regions,
        ["region_id", "region_name"],
        [[1, "north"], [2, "south"]],
    )
    orders2 = orders_main.with_name("orders_region.csv")
    _write_csv(
        orders2,
        ["order_id", "region_id", "amount"],
        [[1, 1, 10.0], [2, 3, 20.0]],
    )
    refs = [
        TableReference(name="customers", path=str(customers_ref), columns={"region_id": "id"}),
        TableReference(name="regions", path=str(regions), columns={"region_id": "region_id"}),
    ]
    context = _make_context(orders2, refs)
    try:
        candidates = DETECTOR.detect(context)
        assert len(candidates) == 2
        assert {c.evidence[0].data["reference"] for c in candidates} == {
            "customers",
            "regions",
        }
        assert all(c.columns == ["region_id"] for c in candidates)
        assert sorted(c.affected_count for c in candidates) == [1, 2]
    finally:
        _close(context)


def test_contract_references_parse(tmp_path: Path, customers_ref: Path) -> None:
    contract = Contract.model_validate(
        {
            "dataset": {"name": "orders"},
            "references": [
                {
                    "name": "customers",
                    "path": str(customers_ref),
                    "columns": {"customer_id": "id"},
                }
            ],
        }
    )
    assert len(contract.references) == 1
    assert contract.references[0].name == "customers"
    assert contract.references[0].columns == {"customer_id": "id"}


def test_scan_file_with_references_end_to_end(
    tmp_path: Path,
    orders_main: Path,
    customers_ref: Path,
) -> None:
    from datasentry import DataSentry

    client = DataSentry(project=tmp_path / "ws")
    try:
        ref = TableReference(
            name="customers",
            path=str(customers_ref),
            columns={"customer_id": "id"},
        )
        scan, _, issues = client.scan_file(orders_main, references=[ref])
        assert scan.status == "completed"
        fk_issues = [i for i in issues if i.issue_type == "integrity_constraint"]
        assert len(fk_issues) == 1
        assert fk_issues[0].affected_count == 1
        assert fk_issues[0].severity == "high"
        assert QualityDimension.INTEGRITY in fk_issues[0].quality_dimensions
    finally:
        client.close()


def test_cli_scan_contract_references(
    tmp_path: Path,
    orders_main: Path,
    customers_ref: Path,
    capsys,
) -> None:
    from datasentry.cli import main

    contract = tmp_path / "c.yaml"
    contract.write_text(
        "version: '1.0'\n"
        "dataset:\n"
        "  name: orders\n"
        "references:\n"
        "  - name: customers\n"
        "    path: " + str(customers_ref) + "\n"
        "    columns:\n"
        "      customer_id: id\n",
        encoding="utf-8",
    )
    code = main(
        [
            "--project",
            str(tmp_path / "ws"),
            "--format",
            "json",
            "scan",
            str(orders_main),
            "--contract",
            str(contract),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["data"]["scan_run_id"]
    from datasentry import DataSentry

    client = DataSentry(project=tmp_path / "ws")
    try:
        issues = client.list_issues(scan_run_id=payload["data"]["scan_run_id"])
        fk_issues = [i for i in issues if i.issue_type == "integrity_constraint"]
        assert len(fk_issues) == 1
        assert fk_issues[0].columns == ["customer_id"]
    finally:
        client.close()
