"""
RevRisk daily pipeline DAG.

WHY THIS SHAPE (the interview questions this answers):

  "Why does Airflow shell out instead of importing dbt/sklearn as Python
  libraries?" Because this project's dbt and ML dependencies live in the
  PROJECT's own virtualenv (revrisk_v2_phase1/.venv), not Airflow's. Airflow
  orchestrates -- it doesn't need dbt-core or scikit-learn installed in its
  own environment, and keeping them separate means upgrading dbt or a
  sklearn version never risks breaking the scheduler. BashOperator calling
  the project venv's python/dbt binaries by absolute path is the standard
  pattern for this.

  "Why generate -> dbt build -> ML in parallel, not all sequential?" The four
  ML scripts (churn, forecast, segmentation, anomaly detection) each read
  independently from the warehouse dbt just built. None of them depend on
  each other's output, so running them in parallel after dbt_build shortens
  the critical path instead of arbitrarily serializing unrelated work.

  "What happens on failure?" Each task gets one retry with backoff (data/ML
  jobs are sometimes transient-flaky -- a locked DuckDB file, a slow pip
  resolve -- not usually a reason to page someone on the first failure).
  dbt build itself is the real gate: if it fails, NO ML task runs, because
  scoring customers against untested data is worse than not scoring them at
  all.

SCHEDULE: daily at 06:00, catchup=False -- this is a portfolio/demo pipeline,
not a production system with a backlog of historical runs to reprocess.
"""

from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator

# orchestration/dags/this_file.py -> project root is two levels up
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_BIN = PROJECT_ROOT / ".venv" / "bin"
DBT_PROJECT_DIR = PROJECT_ROOT / "revrisk_dbt"

default_args = {
    "owner": "revrisk-data-team",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,  # no email infra configured for this project
    "email_on_retry": False,
}

with DAG(
    dag_id="revrisk_daily_pipeline",
    description="Generate source data, build the dbt warehouse, and score all Phase 3 ML models.",
    default_args=default_args,
    schedule="0 6 * * *",
    start_date=datetime(2026, 7, 1),
    catchup=False,
    max_active_runs=1,
    tags=["revrisk", "dbt", "ml"],
) as dag:

    generate_data = BashOperator(
        task_id="generate_source_data",
        bash_command=f"cd {PROJECT_ROOT} && {VENV_BIN}/python data_generator/generate.py",
        doc_md="""
            Regenerates `revrisk_raw.*` in DuckDB. In production this task
            would instead be a real ingestion job (Fivetran, a Python
            extractor, etc.) -- it's a placeholder for "new data landed,"
            not a permanent design choice. Everything downstream doesn't
            care which one it is.
        """,
    )

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=(
            f"cd {DBT_PROJECT_DIR} && "
            f"export DBT_PROFILES_DIR={DBT_PROJECT_DIR} && "
            f"{VENV_BIN}/dbt build --target dev --full-refresh"
        ),
        doc_md="""
            Runs the full dbt DAG (staging -> core -> marts -> snapshot) plus
            every test, including the revenue reconciliation test. This is
            the pipeline's real quality gate: if any test fails, dbt build
            fails, and every downstream ML task is skipped -- Airflow won't
            score customers against data that failed its own tests.

            `--full-refresh` here because `generate_source_data` fully
            replaces the raw tables each run rather than appending
            incrementally (this is a demo pipeline regenerating synthetic
            data, not a real incremental source) -- see fct_transactions.sql
            for the reasoning on when a real incremental source would NOT
            need this.
        """,
    )

    dbt_docs_generate = BashOperator(
        task_id="dbt_docs_generate",
        bash_command=(
            f"cd {DBT_PROJECT_DIR} && "
            f"export DBT_PROFILES_DIR={DBT_PROJECT_DIR} && "
            f"{VENV_BIN}/dbt docs generate --target dev"
        ),
        doc_md="Regenerates the lineage docs site so it never drifts from what actually ran.",
    )

    score_churn = BashOperator(
        task_id="score_churn_model",
        bash_command=f"cd {PROJECT_ROOT}/ml && {VENV_BIN}/python churn_model.py",
        doc_md="Retrains + rescores the churn model, writes main_ml.churn_scores.",
    )

    forecast_revenue = BashOperator(
        task_id="forecast_revenue",
        bash_command=f"cd {PROJECT_ROOT}/ml && {VENV_BIN}/python revenue_forecast.py",
        doc_md="Refits the trend forecast, writes main_ml.revenue_forecast.",
    )

    segment_customers = BashOperator(
        task_id="segment_customers",
        bash_command=f"cd {PROJECT_ROOT}/ml && {VENV_BIN}/python customer_segmentation.py",
        doc_md="Re-clusters customers, writes main_ml.customer_segments.",
    )

    detect_anomalies = BashOperator(
        task_id="detect_transaction_anomalies",
        bash_command=f"cd {PROJECT_ROOT}/ml && {VENV_BIN}/python anomaly_detection.py",
        doc_md="Rescans transactions for leakage anomalies, writes main_ml.transaction_anomalies.",
    )

    pipeline_complete = EmptyOperator(
        task_id="pipeline_complete",
        doc_md="Marker task -- what a downstream DAG (e.g. a dashboard cache warm, a Slack notify) would depend on.",
    )

    # generate -> build -> {docs, 4x ML in parallel} -> complete
    generate_data >> dbt_build
    dbt_build >> [dbt_docs_generate, score_churn, forecast_revenue, segment_customers, detect_anomalies]
    [dbt_docs_generate, score_churn, forecast_revenue, segment_customers, detect_anomalies] >> pipeline_complete
