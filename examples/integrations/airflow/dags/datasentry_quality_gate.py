"""Airflow 集成示例：生成一份含缺陷的 CSV，再用 DataSentry 做质量闸门。

工作目录由 `produce_orders` 在运行期用 `mktemp -d` 创建（0700、路径不可预测），
而不是写死 `/tmp/datasentry-airflow`：固定名字的全局可写路径可能被同机其他用户
预先占位（S5443），并发 DAG run 也会互相覆盖产物。目录路径通过 XCom 传给下游。
"""

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

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
        # 末行输出即 XCom 值（BashOperator 取 stdout 最后一行），供下游拼路径。
        bash_command="""
        set -euo pipefail
        tmp_root="${TMPDIR:-/tmp}"
        tmp_root="${tmp_root%/}"
        data_dir="$(mktemp -d "$tmp_root/datasentry-airflow-XXXXXX")"
        cat > "$data_dir/orders.csv" <<'CSV'
order_id,customer_id,amount,event_date,status
1001,C001,42.50,2026-08-01,paid
1002,C002,18.99,2026-08-02,paid
1003,C003,125.00,2026-08-03,shipped
1004,C004,67.25,2026-02-30,paid
1005,C005,31.10,2026-08-05,shipped
1006,C006,88.40,2026-08-06,paid
CSV
        echo "wrote $data_dir/orders.csv"
        echo "$data_dir"
        """,
    )

    quality_gate = BashOperator(
        task_id="datasentry_quality_gate",
        bash_command="""
        set +e
        data_dir="{{ ti.xcom_pull(task_ids='produce_orders') }}"
        datasentry scan "$data_dir/orders.csv" --fail-on high > "$data_dir/datasentry-result.json"
        code=$?
        set -e

        echo "DataSentry result:"
        cat "$data_dir/datasentry-result.json" || true
        echo
        echo "DataSentry exit code: $code"
        exit "$code"
        """,
    )

    produce_orders >> quality_gate
