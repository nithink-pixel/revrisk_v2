# Phase 4 -- Orchestration

`orchestration/dags/revrisk_pipeline_dag.py` is a real, tested Airflow DAG --
not a code sample. Every task below was executed through actual Airflow
(`airflow tasks test` / `airflow dags test`), not just written and assumed
to work.

## Pipeline shape

```
generate_source_data
        |
        v
    dbt_build   (full DAG: staging -> core -> marts -> snapshot, + 39 tests)
        |
        +------------+------------+------------+------------+
        v            v            v            v            v
  dbt_docs_gen  score_churn  forecast_rev  segment_cust  detect_anomalies
        |            |            |            |            |
        +------------+------------+------------+------------+
                              v
                      pipeline_complete
```

The four ML tasks and the docs task run **in parallel** -- none of them
depend on each other's output, only on `dbt_build` having succeeded. If
`dbt_build` fails (a test fails, a model breaks), nothing downstream runs:
Airflow won't score customers against data that failed its own quality gate.

## Why Airflow lives in its own virtualenv

See `orchestration/requirements-airflow.txt` for the full reasoning. Short
version: Airflow shells out to the project's own `dbt`/`python` binaries via
`BashOperator` rather than importing dbt-core or scikit-learn directly, so
the two dependency trees never conflict.

## Run it yourself

```bash
# One-time setup
python3 -m venv orchestration/.airflow-venv
source orchestration/.airflow-venv/bin/activate
pip install --upgrade pip
pip install -r orchestration/requirements-airflow.txt

export AIRFLOW_HOME=$(pwd)/orchestration/.airflow_home
airflow db migrate

# Point Airflow at the DAG folder
ln -s $(pwd)/orchestration/dags $AIRFLOW_HOME/dags

# Confirm it parses with no errors
airflow dags list-import-errors

# Run the whole pipeline once, end to end, without a live scheduler
airflow dags test revrisk_daily_pipeline $(date +%Y-%m-%d)

# Or run the real thing: webserver + scheduler
airflow webserver --port 8081 &
airflow scheduler &
# then visit http://localhost:8081
```

## What I verified while building this

- `airflow dags list-import-errors` -> no errors
- `airflow tasks list revrisk_daily_pipeline --tree` -> confirmed the
  parallel fan-out shape matches the diagram above
- Every task run individually via `airflow tasks test`, then the full DAG
  via `airflow dags test` -> **all 8 tasks SUCCESS, DAG run SUCCESS**,
  end-to-end in ~21 seconds
- Caught one real bug during this process: the DAG computes its own project
  root from `Path(__file__).resolve().parent.parent.parent`, which broke the
  first time I tested it because I'd copied the file into a flattened test
  directory instead of preserving `orchestration/dags/`. Symlinking the real
  directory (rather than copying the file) fixed it -- worth knowing if you
  ever see this DAG fail with exit code 127 in a new environment: check that
  the file is at its real path, not copied elsewhere.

## Production notes (what would change if this ran for real)

- `generate_source_data` is a stand-in for a real ingestion job (Fivetran, a
  custom extractor, etc.) -- see the task's `doc_md` in the DAG file.
- `dbt_build --full-refresh` runs every time because the demo regenerates
  synthetic raw data each run. A real incremental source would drop
  `--full-refresh` and rely on `fct_transactions`' lookback-window logic
  instead (see that model's own comments for why).
- No email/Slack alerting is configured (`email_on_failure=False`) -- there's
  no alerting infrastructure in this portfolio project. In production, wire
  `on_failure_callback` to whatever the team actually uses.
