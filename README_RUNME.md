# RevRisk v2.0 — Phase 1: run it

> **Status update:** this file originally described Phase 1 only. The project
> has since grown through Phase 3 (ML: churn, forecasting, segmentation,
> anomaly detection -- see `ml/`), Phase 4 (Airflow orchestration -- see
> `orchestration/`), and Phase 5 (Streamlit dashboard -- see `dashboard/`).
> Current verified state: **`dbt build` PASS=50, ERROR=0** (6 staging/core/
> mart tables + views, 1 incremental fact, 1 SCD2 snapshot, 39 tests
> including revenue reconciliation), **10/10 Python unit tests passing**,
> the full Airflow DAG runs end-to-end with all 8 tasks succeeding, and the
> dashboard renders all 6 tabs with zero exceptions (verified via
> Streamlit's `AppTest` framework, not just eyeballed). Run it with
> `streamlit run dashboard/app.py`. The walkthrough below still describes
> the original Phase 1 setup correctly -- it's just no longer the whole
> project.

Verified green: **PASS=30, ERROR=0** (3 staging views, 3 core tables,
1 incremental fact, 1 SCD2 snapshot, 22 tests including revenue reconciliation).

## Run

```bash
cd revrisk_v2

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1. build the sample warehouse
python data_generator/generate.py

# 2. run dbt
cd revrisk_dbt
export DBT_PROFILES_DIR=$(pwd)     # Windows: set DBT_PROFILES_DIR=%cd%
dbt deps
dbt debug                          # expect: All checks passed!
dbt build --target dev             # expect: PASS=30 ERROR=0

# 3. lineage docs
dbt docs generate --target dev
dbt docs serve                     # opens the DAG in your browser
```

## What you should see

```
main_staging     stg_customers / stg_contracts / stg_transactions   VIEW
main_core        dim_customer / dim_date                            TABLE
main_core        fct_transactions                                   TABLE (incremental)
main_analytics   mart_executive_kpis                                TABLE
snapshots        snap_contracts                                     TABLE (SCD2)
```

Reconciliation verified: raw = fact = mart = $17,695,295.42

## Prove the incremental works (do this once, it's your interview story)

```bash
dbt run --select fct_transactions --target dev     # 2nd run: only recent rows
dbt run --select fct_transactions --full-refresh --target dev   # rebuild all
```

## Two real bugs already found and fixed

Both were warehouse-portability failures — worth knowing because they're your
"tell me about a bug" answer:

1. **`to_char()` is Snowflake-only.** DuckDB doesn't have it. `date_key` now uses
   `extract()` arithmetic, which runs identically on both.
2. **Date spine syntax genuinely diverges** — DuckDB `range()`, Snowflake
   `generator()`. `dim_date` branches on `target.type` explicitly rather than
   pretending one syntax covers both.

That's what "warehouse-portable" actually costs. Say that in an interview.

## Next: swap in real RevRisk data

`data_generator/generate.py` is scaffolding. Replace it by pointing
`sources.yml` at your real RevRisk tables and updating column names in
`stg_*.sql`. Everything downstream keeps working.

Add an `ingested_at` column to your loader — `sources.yml` freshness needs it.
