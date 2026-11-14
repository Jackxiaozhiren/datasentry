# Versioned JSON report Schema (v1.0)

DataSentry already exports JSON, Markdown, HTML, JUnit and SARIF. This page documents the **existing JSON quality-report structure**, not a new export format.

The publicly readable [report-v1.schema.json](../packages/core/src/datasentry_core/reporting/report-v1.schema.json) is generated from the versioned Pydantic envelope and the existing nested models for scans, detector runs, issues, and quality scores. It is also packaged with the core Python module.

### Python validation

Using the existing DataSentry dependencies:

    import json
    from datasentry_core.reporting.schema import QualityReportV1

    with open('report.json', encoding='utf-8') as f:
        payload = json.load(f)
    validated = QualityReportV1.model_validate(payload)
    assert validated.report_schema_version == '1.0'

Non-Python clients can use the linked JSON Schema with a conforming validator. Check `report_schema_version` before parsing; do not silently accept an incompatible future schema.

### Maintenance and privacy

Changes to nested models or the envelope must keep the checked-in Schema in sync. The regression test `tests/test_report_json_schema.py` checks equality between the generated and published JSON Schema objects and rejects invalid version fields and undocumented top-level additions. Generate the Schema with `report_v1_json_schema()` using the frozen development environment; create a *new* versioned document for breaking changes rather than silently changing the meaning of 1.0.

Raw JSON reports can contain data-derived evidence. Do not publish one without checking data rights and privacy requirements. JSON's disclosure behavior can differ from existing human-readable output redaction. This schema is a transport contract, **not** a certification of dataset correctness.
