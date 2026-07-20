"""
Phase 3 -- anomaly detection for revenue leakage.

Grain: transaction, not customer. Flags transactions that look statistically
unusual on dimensions that matter for revenue integrity: an unusually large
discount relative to peers on the same plan, a gross amount far outside the
normal range for its transaction_type, or payment failure clustering.

IsolationForest over a handful of interpretable features -- deliberately not
a black-box on raw columns, so each flagged transaction can be explained.

Run:
    python ml/anomaly_detection.py
"""

from pathlib import Path

import duckdb
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from feature_engineering import DB_PATH

CONTAMINATION = 0.03  # expect ~3% of transactions to be genuinely anomalous
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_DIR.mkdir(exist_ok=True)


def load_transactions() -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.execute("""
        with latest_contract as (
            select customer_id, plan,
                   row_number() over (
                       partition by customer_id order by contract_start_date desc
                   ) as rn
            from revrisk_raw.contracts
        )
        select
            t.transaction_id, t.customer_id, t.transaction_date,
            t.transaction_type, t.gross_amount, t.discount_amount,
            t.net_amount, t.discount_rate, t.payment_status,
            coalesce(lc.plan, 'unknown') as plan
        from main_core.fct_transactions t
        left join latest_contract lc on t.customer_id = lc.customer_id and lc.rn = 1
    """).df()
    con.close()
    return df


def main():
    df = load_transactions()
    print(f"Loaded {len(df)} transactions")

    # Features: relative to plan peers where it matters, so a $50K Enterprise
    # transaction and a $50K Basic transaction are judged on different scales.
    df["gross_amount_zscore_by_plan"] = df.groupby("plan")["gross_amount"].transform(
        lambda x: (x - x.mean()) / max(x.std(ddof=0), 1e-6)
    )
    df["is_failed_payment"] = (df["payment_status"] == "failed").astype(int)

    feature_cols = ["gross_amount_zscore_by_plan", "discount_rate", "is_failed_payment"]
    X = df[feature_cols].fillna(0)
    X_scaled = StandardScaler().fit_transform(X)

    iso = IsolationForest(contamination=CONTAMINATION, random_state=42, n_estimators=300)
    df["anomaly_flag"] = iso.fit_predict(X_scaled) == -1
    df["anomaly_score"] = -iso.score_samples(X_scaled)  # higher = more anomalous

    flagged = df[df["anomaly_flag"]].sort_values("anomaly_score", ascending=False)
    print(f"\nFlagged {len(flagged)} transactions ({len(flagged) / len(df):.1%})")
    print("\nTop 10 most anomalous:")
    print(flagged[[
        "transaction_id", "customer_id", "transaction_type", "gross_amount",
        "discount_rate", "payment_status", "anomaly_score",
    ]].head(10).to_string(index=False))

    print("\nFlagged transactions by type:")
    print(flagged["transaction_type"].value_counts())

    leaked_value = flagged.loc[flagged["net_amount"] < 0, "net_amount"].sum()
    print(f"\nNet negative value among flagged transactions (refund/leakage exposure): ${leaked_value:,.2f}")

    output = df[[
        "transaction_id", "customer_id", "transaction_date", "transaction_type",
        "gross_amount", "discount_rate", "payment_status", "anomaly_flag",
        "anomaly_score",
    ]]

    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE SCHEMA IF NOT EXISTS main_ml")
    con.register("anomaly_tmp", output)
    con.execute("CREATE OR REPLACE TABLE main_ml.transaction_anomalies AS SELECT * FROM anomaly_tmp")
    con.unregister("anomaly_tmp")
    con.close()

    print(f"\nWrote {len(output)} scored transactions to main_ml.transaction_anomalies")


if __name__ == "__main__":
    main()
