"""
Phase 3 -- shared feature engineering.

Deliberately reads from the dbt-built tables (main_core, main_analytics, raw
contracts), not from generate.py's dataframes directly. That's the point:
the ML layer sits downstream of the tested, documented dbt models, the same
way it would against a real warehouse. If a dbt test fails, this layer
shouldn't be trusted either.

Label definition:
    Churn lives on the CONTRACT (renewal_status), not the customer. A customer
    is labeled churned if their most recent contract (by contract_start_date)
    has renewal_status = 'churned'. This mirrors reality: SaaS churn is a
    contract-level event that rolls up to a customer-level label.
"""

from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "revrisk_dev.duckdb"


def get_connection(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def build_customer_features() -> pd.DataFrame:
    """One row per customer: churn label + behavioral/contractual features.

    Only uses information that would have been knowable at scoring time in a
    real system (i.e. no post-churn transaction behavior leaking into the
    features) -- recency/frequency/monetary features are computed from all
    transactions on file, which is standard practice for a periodic batch
    scoring job (score customers on their behavior-to-date).
    """
    con = get_connection()

    customers = con.execute("""
        select customer_id, industry, segment, region, account_owner, signup_date
        from main_core.dim_customer
    """).df()

    # Most recent contract per customer -> current plan + churn label
    latest_contract = con.execute("""
        with ranked as (
            select
                customer_id,
                plan,
                contract_value,
                discount_rate,
                billing_frequency,
                renewal_status,
                contract_start_date,
                row_number() over (
                    partition by customer_id order by contract_start_date desc
                ) as rn
            from revrisk_raw.contracts
        )
        select customer_id, plan, contract_value, discount_rate,
               billing_frequency, renewal_status
        from ranked
        where rn = 1
    """).df()

    contract_counts = con.execute("""
        select customer_id, count(*) as contract_count
        from revrisk_raw.contracts
        group by 1
    """).df()

    txn_features = con.execute("""
        select
            customer_id,
            count(*) as txn_count,
            sum(net_amount) as total_net_revenue,
            avg(net_amount) as avg_txn_value,
            avg(discount_rate) as avg_discount_rate,
            sum(case when payment_status = 'failed' then 1 else 0 end) as failed_payment_count,
            sum(case when transaction_type = 'refund' then 1 else 0 end) as refund_count,
            max(transaction_date) as last_transaction_date,
            min(transaction_date) as first_transaction_date
        from main_core.fct_transactions
        group by 1
    """).df()

    con.close()

    df = (
        customers
        .merge(latest_contract, on="customer_id", how="left")
        .merge(contract_counts, on="customer_id", how="left")
        .merge(txn_features, on="customer_id", how="left")
    )

    # Derived features
    as_of = pd.Timestamp("2026-07-01")  # matches generate.py's `now`
    df["signup_date"] = pd.to_datetime(df["signup_date"])
    df["last_transaction_date"] = pd.to_datetime(df["last_transaction_date"])
    df["tenure_days"] = (as_of - df["signup_date"]).dt.days
    df["recency_days"] = (as_of - df["last_transaction_date"]).dt.days
    df["failed_payment_rate"] = (
        df["failed_payment_count"] / df["txn_count"].replace(0, pd.NA)
    ).fillna(0)
    df["refund_rate"] = (
        df["refund_count"] / df["txn_count"].replace(0, pd.NA)
    ).fillna(0)

    df["is_churned"] = (df["renewal_status"] == "churned").astype(int)

    df = df.fillna({
        "txn_count": 0, "total_net_revenue": 0, "avg_txn_value": 0,
        "avg_discount_rate": 0, "failed_payment_count": 0, "refund_count": 0,
        "recency_days": df["tenure_days"],  # never transacted -> as stale as tenure
        # customers with zero contracts on file (leads / never-converted):
        # real data quality reality, not a bug -- impute to "no contract" state
        "contract_value": 0, "discount_rate": 0, "contract_count": 0,
        "plan": "none", "billing_frequency": "none",
    })

    return df


FEATURE_COLUMNS_NUMERIC = [
    "contract_value", "discount_rate", "contract_count", "tenure_days",
    "recency_days", "txn_count", "total_net_revenue", "avg_txn_value",
    "avg_discount_rate", "failed_payment_count", "failed_payment_rate",
    "refund_count", "refund_rate",
]

FEATURE_COLUMNS_CATEGORICAL = [
    "industry", "segment", "region", "plan", "billing_frequency",
]


if __name__ == "__main__":
    features = build_customer_features()
    print(f"Built features for {len(features)} customers")
    print(f"Churn rate: {features['is_churned'].mean():.1%}")
    print(features[FEATURE_COLUMNS_NUMERIC + ["is_churned"]].describe().T)
