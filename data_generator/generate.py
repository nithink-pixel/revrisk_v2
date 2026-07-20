"""
RevRisk v2.0 - sample raw data generator.

PURPOSE: get `dbt build` to green BEFORE wiring in real RevRisk data.
Prove the plumbing (sources -> staging -> facts -> marts -> tests) works on data
you control, then swap this out for your actual RevRisk tables.

Creates the three raw tables that sources.yml declares:
    revrisk_raw.customers
    revrisk_raw.contracts
    revrisk_raw.transactions

Run:  python data_generator/generate.py
"""

import duckdb
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

SEED = 42
N_CUSTOMERS = 500
N_CONTRACTS = 600
N_TRANSACTIONS = 8000

rng = np.random.default_rng(SEED)

DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "revrisk_dev.duckdb"

REGIONS = ["Northeast", "Southeast", "Midwest", "West"]
SEGMENTS = ["Enterprise", "Mid-Market", "SMB"]
INDUSTRIES = ["Technology", "Healthcare", "Finance", "Retail", "Manufacturing"]
PLANS = ["Basic", "Professional", "Enterprise", "Premium"]
OWNERS = [f"owner_{i:02d}" for i in range(1, 13)]
TXN_TYPES = ["subscription", "expansion", "refund", "credit", "renewal", "service_fee"]
PAY_STATUS = ["paid", "pending", "failed"]

now = datetime(2026, 7, 1)


def make_customers() -> pd.DataFrame:
    return pd.DataFrame({
        "customer_id": [f"CUST_{i:05d}" for i in range(1, N_CUSTOMERS + 1)],
        "customer_name": [f"Customer {i}" for i in range(1, N_CUSTOMERS + 1)],
        "industry": rng.choice(INDUSTRIES, N_CUSTOMERS),
        "segment": rng.choice(SEGMENTS, N_CUSTOMERS, p=[0.2, 0.35, 0.45]),
        "region": rng.choice(REGIONS, N_CUSTOMERS),
        "signup_date": [
            (now - timedelta(days=int(d))).date()
            for d in rng.integers(90, 1095, N_CUSTOMERS)
        ],
        "account_owner": rng.choice(OWNERS, N_CUSTOMERS),
        "ingested_at": now,
    })



# Churn is driven by a latent risk score, not pure noise. This is what makes
# the Phase 3 churn model learnable: SMB accounts, heavily-discounted deals,
# and cheaper plans genuinely carry more churn risk here, same as most real
# SaaS books of business. The risk score itself is NEVER written to a raw
# table -- only its downstream *consequences* are (renewal_status, and in
# make_transactions, payment/refund behavior). A model trained on those
# consequences has to actually learn the pattern, not read the label.
_SEGMENT_RISK = {"Enterprise": -0.6, "Mid-Market": 0.0, "SMB": 0.7}
_PLAN_RISK = {"Basic": 0.35, "Professional": 0.05, "Enterprise": -0.35, "Premium": -0.1}


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def make_contracts(customers: pd.DataFrame) -> pd.DataFrame:
    cust_ids = rng.choice(customers["customer_id"], N_CONTRACTS)
    starts = [(now - timedelta(days=int(d))).date() for d in rng.integers(30, 730, N_CONTRACTS)]
    plans = rng.choice(PLANS, N_CONTRACTS)
    discount_rate = np.round(rng.beta(2, 12, N_CONTRACTS), 4)

    segment_by_customer = customers.set_index("customer_id")["segment"]
    segments = segment_by_customer.loc[cust_ids].values

    segment_risk = np.array([_SEGMENT_RISK[s] for s in segments])
    plan_risk = np.array([_PLAN_RISK[p] for p in plans])
    noise = rng.normal(0, 0.5, N_CONTRACTS)

    # Heavier discounts correlate with churn (price-sensitive accounts that
    # were bought, not sold, tend to leave). Centered so the average discount
    # contributes roughly zero.
    discount_risk = 3.0 * (discount_rate - discount_rate.mean())

    risk_score = segment_risk + plan_risk + discount_risk + noise
    # calibrate the intercept so overall churn rate lands near 10%, matching
    # the original design target
    churn_prob = _sigmoid(risk_score - 1.6)
    is_churned = rng.uniform(0, 1, N_CONTRACTS) < churn_prob

    # Among non-churned contracts, split active/pending roughly 78/22,
    # with higher-risk non-churned accounts more likely "pending" (i.e. on
    # thin ice, a genuine leading indicator).
    pending_prob = _sigmoid(risk_score - 0.8)
    is_pending = (~is_churned) & (rng.uniform(0, 1, N_CONTRACTS) < pending_prob)

    renewal_status = np.where(
        is_churned, "churned", np.where(is_pending, "pending", "active")
    )

    return pd.DataFrame({
        "contract_id": [f"CTR_{i:05d}" for i in range(1, N_CONTRACTS + 1)],
        "customer_id": cust_ids,
        "plan": plans,
        "contract_start_date": starts,
        # end always after start -- satisfies the date-order business test
        "contract_end_date": [s + timedelta(days=365) for s in starts],
        "billing_frequency": rng.choice(["monthly", "quarterly", "annual"], N_CONTRACTS),
        "contract_value": np.round(rng.lognormal(10.2, 0.8, N_CONTRACTS), 2),
        "discount_rate": discount_rate,
        "renewal_status": renewal_status,
        "account_owner": rng.choice(OWNERS, N_CONTRACTS),
        "ingested_at": now,
        # internal only -- dropped before persisting, used by make_transactions
        # to make behavioral signals (failed payments, refunds, engagement)
        # consistent with the same underlying risk that drove renewal_status.
        "_risk_score": risk_score,
    })


def make_transactions(contracts: pd.DataFrame) -> pd.DataFrame:
    # Risk-weighted transaction volume: at-risk contracts show LOWER engagement
    # (fewer transactions), which is a real, learnable behavioral signal, not
    # just a label restated. Weights are relative, then sampled to hit
    # N_TRANSACTIONS total.
    engagement_weight = _sigmoid(-1.0 * contracts["_risk_score"].values) + 0.15
    sample_p = engagement_weight / engagement_weight.sum()
    idx = rng.choice(len(contracts), size=N_TRANSACTIONS, p=sample_p)
    picked = contracts.iloc[idx].reset_index(drop=True)
    picked_risk = picked["_risk_score"].values
    picked_churn_status = picked["renewal_status"].values

    txn_type = rng.choice(TXN_TYPES, N_TRANSACTIONS, p=[0.45, 0.12, 0.08, 0.05, 0.22, 0.08])

    # Churned/high-risk contracts skew toward refunds over expansions --
    # nudge transaction_type per-row using the same risk score.
    risky = picked_risk > np.percentile(picked_risk, 75)
    flip_to_refund = risky & (rng.uniform(0, 1, N_TRANSACTIONS) < 0.25) & (txn_type == "expansion")
    txn_type = np.where(flip_to_refund, "refund", txn_type)

    gross = np.round(rng.lognormal(7.5, 1.0, N_TRANSACTIONS), 2)

    # discount_amount <= gross_amount so discount_rate stays within [0,1]
    disc_rate = rng.beta(2, 12, N_TRANSACTIONS)
    discount = np.round(gross * disc_rate, 2)

    # net = gross - discount. This is what the reconciliation test verifies.
    net = np.round(gross - discount, 2)

    # refunds are negative net -- realistic, and exercises the refund tests
    refund_mask = txn_type == "refund"
    net[refund_mask] = -net[refund_mask]

    # Failed payments are more likely on higher-risk contracts -- another
    # behavioral consequence of the same latent risk, not a copy of the label.
    fail_prob = np.clip(_sigmoid(picked_risk - 1.2) * 0.35, 0.02, 0.4)
    pending_prob = np.full(N_TRANSACTIONS, 0.08)
    paid_prob = 1 - fail_prob - pending_prob
    payment_status = np.array([
        rng.choice(PAY_STATUS, p=[paid_prob[i], pending_prob[i], fail_prob[i]])
        for i in range(N_TRANSACTIONS)
    ])

    # Churned contracts' transactions cluster earlier (declining recency) --
    # they stop transacting well before "now", which is a real recency signal
    # a churn model should pick up on. Both groups still span the SAME overall
    # 540-day window (just with a higher floor for churned), so this shifts
    # recency without distorting the total monthly revenue distribution the
    # way a narrower window for the majority (active/pending) class would.
    max_days_back = np.full(N_TRANSACTIONS, 540)
    min_days_back = np.where(picked_churn_status == "churned", 90, 0)
    txn_days_back = np.array([
        rng.integers(min_days_back[i], max_days_back[i] + 1) for i in range(N_TRANSACTIONS)
    ])

    return pd.DataFrame({
        "transaction_id": [f"TXN_{i:07d}" for i in range(1, N_TRANSACTIONS + 1)],
        "customer_id": picked["customer_id"].values,
        "contract_id": picked["contract_id"].values,
        "invoice_id": [f"INV_{i:07d}" for i in range(1, N_TRANSACTIONS + 1)],
        # no future dates -- satisfies that business test
        "transaction_date": [
            (now - timedelta(days=int(d))).date() for d in txn_days_back
        ],
        "transaction_type": txn_type,
        "gross_amount": gross,
        "discount_amount": discount,
        "net_amount": net,
        "payment_status": payment_status,
        "ingested_at": now,
    })


def main():
    customers = make_customers()
    contracts = make_contracts(customers)
    transactions = make_transactions(contracts)

    # _risk_score is generator-internal only (used to make renewal_status and
    # transaction behavior consistent with each other) -- never persisted.
    contracts_to_persist = contracts.drop(columns=["_risk_score"])

    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE SCHEMA IF NOT EXISTS revrisk_raw")

    for name, df in [
        ("customers", customers),
        ("contracts", contracts_to_persist),
        ("transactions", transactions),
    ]:
        con.register("tmp_df", df)
        con.execute(f"CREATE OR REPLACE TABLE revrisk_raw.{name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
        print(f"  revrisk_raw.{name}: {len(df):,} rows")

    con.close()
    print(f"\nWarehouse ready: {DB_PATH}")


if __name__ == "__main__":
    main()
