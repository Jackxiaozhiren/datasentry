"""规则引擎（Step 28，14.1 规则模型 + 14.3 预运行落地）。

MVP 时期只实现「用户/契约/LLM 候选规则」的预运行与审批，不接入
扫描主流程（检测器仍为主扫描路径；规则引擎执行归属 V1 后续）。

- `build_violation_sql(rule)`：把 Rule.when **期望条件**（14.1：规则描述数据应
  满足的约束）翻译为参数化「违规子句」——期望条件取反（equals→违规为
  不相等；between→违规为不在区间），列名白名单校验 + 值参数绑定防注入
- `run_preflight(rule, handle)`：预运行（14.3）——统计 rows_tested /
  failures / failure_ratio，并取前 N 行违反样本
"""

from __future__ import annotations

from typing import Any

from datasentry_core.connectors.base import DataHandle
from datasentry_core.models.rules import (
    Condition,
    Rule,
    RulePreflightReport,
    RulePreflightSampleRun,
)

_OPS: dict[str, str] = {
    "equals": "col <> $1",
    "not_equals": "col = $1",
    "gt": "col <= $1",
    "gte": "col < $1",
    "lt": "col >= $1",
    "lte": "col > $1",
    "not_null": "col IS NULL",
    "is_null": "col IS NOT NULL",
    "matches": "NOT REGEXP_MATCHES(CAST(col AS VARCHAR), $1)",
    "between": "col NOT BETWEEN $1 AND $2",
    "not_between": "col BETWEEN $1 AND $2",
    "in": "col NOT IN ($1)",
    "not_in": "col IN ($1)",
}

_EXAMPLE_LIMIT = 5


def _condition_sql(condition: Condition) -> tuple[str, dict[str, Any]]:
    """翻译 Condition → （参数化 SQL 片段, 命名绑定值）。列名经引号转义。"""
    column = f'"{condition.column}"'
    template = _OPS.get(condition.operator)
    if template is None:
        raise ValueError(f"unsupported operator: {condition.operator}")
    sql = template.replace("col", column)
    if condition.operator in {"in", "not_in"}:
        values = condition.value
        if not isinstance(values, list) or not values:
            raise ValueError(f"{condition.operator} requires a non-empty list value")
        placeholders = ", ".join(f"${i + 1}" for i in range(len(values)))
        params = {str(i + 1): v for i, v in enumerate(values)}
        return sql.replace("($1)", f"({placeholders})"), params
    if condition.operator in {"between", "not_between"}:
        values = condition.value
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError(f"{condition.operator} requires exactly 2 values")
        return sql, {"1": values[0], "2": values[1]}
    if condition.operator in {"is_null", "not_null"}:
        return sql, {}
    return sql, {"1": condition.value}


def _violation_sql(rule: Rule) -> tuple[str, dict[str, Any]]:
    if rule.when is None:
        raise ValueError(f"rule {rule.id}: when condition is required for preflight")
    return _condition_sql(rule.when)


def _columns_exist(rule: Rule, columns: set[str]) -> list[str]:
    """校验规则引用的列存在（14.3 schema_valid）。返回缺失列列表。"""
    missing: list[str] = []
    if rule.when and rule.when.column not in columns:
        missing.append(rule.when.column)
    for column in rule.columns:
        if column not in columns:
            missing.append(column)
    return sorted(set(missing))


def run_preflight(rule: Rule, handle: DataHandle) -> RulePreflightReport:
    """规则预运行（14.3）：schema 校验 + 样本执行 + 违规统计。"""
    columns = set(handle.schema().column_names)
    missing = _columns_exist(rule, columns)
    if missing:
        return RulePreflightReport(
            rule_id=rule.id,
            valid=False,
            schema_valid=False,
            columns_exist=[c for c in rule.columns if c in columns],
            dangerous=False,
        )
    try:
        where_sql, params = _violation_sql(rule)
    except ValueError as exc:
        _ = exc
        return RulePreflightReport(
            rule_id=rule.id,
            valid=False,
            schema_valid=True,
            dangerous=False,
            sample_run=RulePreflightSampleRun(rows_tested=0, failures=0, failure_ratio=0.0),
        )
    row_count = handle.count_rows()
    failures_table = handle.sql_aggregate(
        f"SELECT count(*) AS n FROM data WHERE {where_sql}", params
    ).table
    failures = int(failures_table.column(0)[0])
    sample: list[str] = []
    if failures > 0:
        sample_table = handle.sql_aggregate(
            f"SELECT * FROM data WHERE {where_sql} LIMIT {_EXAMPLE_LIMIT}", params
        ).table
        for row in sample_table.to_pylist():
            sample.append(str(row))
    ratio = failures / row_count if row_count > 0 else 0.0
    return RulePreflightReport(
        rule_id=rule.id,
        valid=True,
        schema_valid=True,
        columns_exist=[c for c in rule.columns if c in columns],
        dangerous=failures > 0 and ratio > 0.5,
        sample_run=RulePreflightSampleRun(
            rows_tested=row_count,
            failures=failures,
            failure_ratio=ratio,
            example_rows=sample,
        ),
    )
