RevRisk v2.0 — Revenue & Risk Intelligence Platform

A warehouse-portable analytics platform that turns raw transactions into governed,
trustworthy revenue-risk intelligence — and proves every number reconciles to the
exact dollar.

🔗 Live Demo  |  Built with dbt · Python · DuckDB / Snowflake · Streamlit


The problem

Most analytics projects show you what happened. Very few can prove the number is
correct. In six years of building reporting for finance and operations teams, the
question I heard most from leadership was simple: "Can we trust this?"

RevRisk v2.0 is built to answer that — a revenue-risk platform where every figure on
the dashboard traces back to a tested, documented dbt model, and the executive revenue
number provably matches the raw source to the cent.


What it does

CapabilityWhat it deliversExecutive OverviewNet revenue, active customers, effective discount rate, failed-payment value, monthly trend, revenue by region and segmentRevenue LeakageDetects and quantifies leakage (discount, refund, failed payment) — surfaces top leaking transactions with drill-downCustomer Health & ChurnRule-based health score and an ML churn model (0.88 ROC-AUC) shown side by side as two decision lensesSegmentsCustomer segmentation (RFM-style clustering) into actionable groups: Healthy High-Value, Disengaged Low-Value, No Active ContractAnomaliesStatistical anomaly detection on revenue and operational metricsForecastNet revenue history vs. forecast (Holt's linear trend), with in-sample MAPE of 11.9%



Dashboard

Executive Overview — governed KPIs and revenue trend, all traced to tested dbt models
Show Image

Revenue Leakage — quantifies discount, refund, and failed-payment leakage with transaction-level drill-down
Show Image

Forecast — net revenue history vs. forecast (Holt's linear trend), in-sample MAPE 11.9%
Show Image

Customer Health & Churn — rule-based health score and an ML churn model (0.88 ROC-AUC) as two decision lenses
Show Image

Architecture

Raw transaction data
        │
        ▼
  dbt staging (views)        ← rename, cast, standardize
        │
        ▼
  dbt intermediate           ← business logic, reusable joins
        │
        ▼
  Core warehouse             ← star schema: dim_customer, dim_date,
  (dimensions + facts)         dim_region, fct_transactions (incremental),
        │                      snap_contracts (SCD Type 2)
        ▼
  Analytics marts            ← governed KPIs, leakage, health, segments
        │
        ▼
  Streamlit dashboard        ← reads ONLY from governed marts

Design principle: SQL and dbt calculate the truth; the dashboard communicates it.
The app never computes a metric on the fly — it reads governed marts, so every number
is defined once, tested, and documented.


What makes it trustworthy


Star-schema dimensional model — dimensions and fact tables with defined grain
Incremental fact table — fct_transactions processes only new/changed rows,
with a lookback window to absorb late-arriving records
SCD Type 2 snapshot — contract history preserved so historical reports stay
correct when terms change
39 automated data-quality tests — generic (unique, not-null, relationships,
accepted-values) plus singular business tests
Exact-dollar reconciliation — a custom test proves revenue matches across raw →
fact → mart; if it ever diverges, the build fails
Warehouse-portable — the same dbt project runs on DuckDB (free public demo) and
Snowflake (cloud), with materializations chosen per layer
Lineage + documentation — dbt docs generates the full model dependency graph
CI with GitHub Actions — every pull request compiles, builds, and tests the
project before it can merge



Tech stack

Transformation: dbt (staging → intermediate → core → marts)
Warehouse: DuckDB (demo) / Snowflake (portable)
Analysis & ML: Python, pandas, scikit-learn (churn model), statistical anomaly
detection, Holt's linear trend forecast
App: Streamlit + Plotly
Orchestration & CI: Airflow, GitHub Actions


Running it locally

bash# 1. environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. generate the sample warehouse
python data_generator/generate.py

# 3. build the dbt project
cd revrisk_dbt
export DBT_PROFILES_DIR=$(pwd)
dbt deps
dbt build --target dev        # runs all models + 39 tests

# 4. view lineage
dbt docs generate && dbt docs serve

# 5. launch the dashboard
cd .. && streamlit run app.py


Repository structure

revrisk/
├── revrisk_dbt/            # dbt project
│   ├── models/
│   │   ├── staging/        # views: rename, cast, standardize
│   │   ├── intermediate/   # reusable business logic
│   │   ├── core/           # dims + incremental facts (star schema)
│   │   └── marts/          # governed KPI / leakage / health / segment marts
│   ├── snapshots/          # SCD Type 2 contract history
│   ├── tests/              # singular tests incl. exact-dollar reconciliation
│   └── dbt_project.yml
├── ml/
│   └── churn_model.py      # ML churn model (0.88 ROC-AUC)
├── analytics/              # anomaly detection, forecasting, segmentation
├── data_generator/         # reproducible sample data
├── app.py                  # Streamlit dashboard (reads governed marts only)
├── .github/workflows/      # CI: build + test on every PR
└── requirements.txt


Notes on the data

The public demo runs on a reproducible synthetic dataset generated by
data_generator/. It's engineered to be realistic — seasonality, segment-driven deal
sizes, and deliberate data-quality issues so the validation tests and leakage detection
have something real to catch. No production or proprietary data is used.


Why v2.0

The first version answered a business question and worked as a decision-support tool.
v2.0 rebuilt the foundation with the discipline a real enterprise reporting system
demands: a tested transformation layer, dimensional modeling, exact-dollar
reconciliation, lineage, and CI. The lesson from v1 → v2: useful and trustworthy
are not the same thing, and the second one is what actually gets used.


Built by Nithin Krishna — M.S. Business
Analytics, UMass Amherst. Open to Data Analyst / Business Intelligence roles.
