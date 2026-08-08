"""Step 37 契约导出器测试（V1：Pandera 代码生成 / GE ExpectationSuite）。"""

from __future__ import annotations

from datasentry_core.contracts import to_great_expectations, to_pandera
from datasentry_core.models.contract import ColumnCheck, ColumnContract, Contract, DatasetContract
from datasentry_core.models.enums import RuleType, Severity
from datasentry_core.models.rules import Condition, Rule


def _contract() -> Contract:
    return Contract(
        version="1.0",
        dataset=DatasetContract(
            name="orders",
            description="ecommerce orders",
            primary_key=["order_id"],
        ),
        columns={
            "order_id": ColumnContract(
                type="integer",
                nullable=False,
                unique=True,
                min=1,
            ),
            "amount": ColumnContract(
                type="float",
                min=0.0,
                max=100000.0,
                checks=[ColumnCheck(type="range", min=0.0, max=100000.0)],
            ),
            "status": ColumnContract(
                type="string",
                allowed_values=["pending", "paid", "shipped", "cancelled"],
            ),
            "email": ColumnContract(
                type="string",
                regex=r"^[^@]+@[^@]+$",
            ),
        },
        rules=[
            Rule(
                id="r_positive_amount",
                type=RuleType.VALUE_RANGE,
                severity=Severity.HIGH,
                when=Condition(column="amount", operator="between", value=[0.0, 100000.0]),
                columns=["amount"],
            ),
            Rule(
                id="r_status_set",
                type=RuleType.ALLOWED_VALUES,
                when=Condition(column="status", operator="in", value=["paid", "shipped"]),
                columns=["status"],
            ),
            Rule(
                id="r_email_format",
                type=RuleType.REGEX,
                when=Condition(column="email", operator="matches", value=r"@"),
                columns=["email"],
            ),
            Rule(
                id="r_aggregate",
                type=RuleType.AGGREGATE,
                columns=["amount"],
                expression="avg(amount) >= 0",
            ),
        ],
    )


class TestPandera:
    def test_schema_structure(self) -> None:
        code = to_pandera(_contract())
        assert "import pandera as pa" in code
        assert "import pandas as pd" in code
        assert "schema = pa.DataFrameSchema({" in code
        assert "'order_id': pa.Column(" in code
        assert "'email': pa.Column(" in code

    def test_column_attributes(self) -> None:
        code = to_pandera(_contract())
        order_block = code.split("'order_id': pa.Column(")[1].split("    ),")[0]
        assert "dtype=pa.Int64()" in order_block
        assert "nullable=False" in order_block
        assert "unique=True" in order_block
        assert "pa.Check.ge(1.0, title='order_id_min')" in order_block

    def test_checks_and_rules(self) -> None:
        code = to_pandera(_contract())
        amount_block = code.split("'amount': pa.Column(")[1].split("    ),")[0]
        assert "pa.Check.ge(0.0, title='amount_min')" in amount_block
        assert "pa.Check.le(100000.0, title='amount_max')" in amount_block
        assert "pa.Check.isin(['pending', 'paid', 'shipped', 'cancelled']" in code
        assert "pa.Check.str_matches('^[^@]+@[^@]+$'" in code
        assert "pa.Check.ge(0.0) & pa.Check.le(100000.0)" in code  # r_positive_amount
        assert "pa.Check.isin(['paid', 'shipped'])" in code  # r_status_set
        assert "pa.Check.str_matches('@')" in code  # r_email_format

    def test_unsupported_rule_as_comment(self) -> None:
        code = to_pandera(_contract())
        assert "# unsupported rule 'r_aggregate' (type=aggregate)" in code

    def test_nullable_default_true_no_attrs(self) -> None:
        contract = _contract()
        contract.columns["email"] = ColumnContract(type="string")
        contract.rules = [r for r in contract.rules if "email" not in r.columns]
        code = to_pandera(contract)
        block = code.split("'email': pa.Column(")[1].split("    ),")[0]
        assert "nullable=False" not in block
        assert "unique=" not in block
        assert "checks=[" not in block


class TestGreatExpectations:
    def test_suite_envelope(self) -> None:
        suite = to_great_expectations(_contract())
        assert suite["expectation_suite_name"] == "orders.datasentry"
        assert suite["meta"]["datasentry_version"] == "1.0"
        assert suite["meta"]["description"] == "ecommerce orders"

    def test_column_expectations(self) -> None:
        suite = to_great_expectations(_contract())
        types = {e["expectation_type"] for e in suite["expectations"]}
        assert "expect_column_values_to_be_between" in types
        assert "expect_column_values_to_not_be_null" in types
        assert "expect_column_values_to_be_unique" in types
        assert "expect_column_values_to_be_in_set" in types
        assert "expect_column_values_to_match_regex" in types
        assert "expect_column_values_to_be_of_type" in types

    def test_between_kwargs_from_rule(self) -> None:
        suite = to_great_expectations(_contract())
        between = [
            e
            for e in suite["expectations"]
            if e["expectation_type"] == "expect_column_values_to_be_between"
        ]
        amount = next(e for e in between if e["kwargs"]["column"] == "amount")
        assert amount["kwargs"]["min_value"] == 0.0
        assert amount["kwargs"]["max_value"] == 100000.0

    def test_rule_mapping(self) -> None:
        suite = to_great_expectations(_contract())
        in_set = [
            e["kwargs"]["value_set"]
            for e in suite["expectations"]
            if e["expectation_type"] == "expect_column_values_to_be_in_set"
            and e["kwargs"]["column"] == "status"
        ]
        assert ["paid", "shipped"] in in_set
        regex = [
            e["kwargs"]["regex"]
            for e in suite["expectations"]
            if e["expectation_type"] == "expect_column_values_to_match_regex"
            and e["kwargs"]["column"] == "email"
        ]
        assert "@" in regex

    def test_conditional_value_rule_unsupported(self) -> None:
        contract = _contract()
        contract.rules = [
            Rule(
                id="r_cond",
                type=RuleType.CONDITIONAL_VALUE,
                when=Condition(column="status", operator="equals", value="paid"),
                then=Condition(column="amount", operator="gt", value=0),
                columns=["status"],
            )
        ]
        suite = to_great_expectations(contract)
        baseline = to_great_expectations(Contract(**{**contract.model_dump(), "rules": []}))
        assert suite["expectations"] == baseline["expectations"]  # 规则不可表达 → 零贡献
