from __future__ import annotations

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

DATA_DIR = "/tmp/datasentry-airflow"
DATA_PATH = f"{DATA_DIR}/orders.csv"
RESULT_PATH = f"{DATA_DIR}/datasentry-result.json"

with DAG(
    dag_id="datasentry_quality_gate_demo",
    description="Produce local data and gate the pipeline with DataSentry",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["datasentry", "data-quality"],
) as dag:
    produce_orders = BashOperator(
        task_id="produce_orders",
        bash_command=f"""
        set -euo pipefail
        mkdir -p {DATA_DIR}
        cat > {DATA_PATH} <<'CSV'
order_id,customer_id,amount,event_date,status
1001,C001,42.50,2026-08-01,paid
1002,C002,18.99,2026-08-02,paid
1003,C003,125.00,2026-08-03,shipped
1004,C004,67.25,2026-02-30,paid
1005,C005,31.10,2026-08-05,shipped
1006,C006,88.40,2026-08-06,paid
CSV
        echo "wrote {DATA_PATH}"
        """,
    )

    quality_gate = BashOperator(
        task_id="datasentry_quality_gate",
        bash_command=f"""
        set +e
        datasentry scan {DATA_PATH} --fail-on high > {RESULT_PATH}
        code=$?
        set -e

        echo "DataSentry result:"
        cat {RESULT_PATH} || true
        echo
        echo "DataSentry exit code: $code"
        exit "$code"
        """,
    )

    produce_orders >> quality_gate
