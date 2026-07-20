"""
Phase 3 -- customer segmentation.

Unsupervised clustering on behavioral + value features (not the given
business `segment` column -- the point is to discover structure the business
labels might be missing, e.g. a high-value-but-at-risk cluster that spans
multiple official segments).

Run:
    python ml/customer_segmentation.py
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.impute import SimpleImputer
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from feature_engineering import DB_PATH, build_customer_features

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_DIR.mkdir(exist_ok=True)

CLUSTER_FEATURES = [
    "total_net_revenue", "avg_txn_value", "txn_count", "recency_days",
    "tenure_days", "avg_discount_rate", "failed_payment_rate", "refund_rate",
    "contract_value",
]


def choose_k(X_scaled, k_range=range(2, 8)) -> int:
    """Pick k by silhouette score -- more principled than eyeballing an elbow
    plot, and easy to justify in an interview: 'I didn't guess k=4, I tested
    2 through 7 and this had the best-separated clusters.'"""
    scores = {}
    for k in k_range:
        labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(X_scaled)
        scores[k] = silhouette_score(X_scaled, labels)
    best_k = max(scores, key=scores.get)
    print("Silhouette scores by k:", {k: round(v, 3) for k, v in scores.items()})
    return best_k


def label_cluster(row: pd.Series) -> str:
    """Human-readable label from cluster centroid characteristics, not just
    'Cluster 0/1/2/3' -- this is what makes the output usable by a non-technical
    stakeholder (or a dashboard)."""
    if row["total_net_revenue"] > row["_rev_median"] and row["failed_payment_rate"] < row["_fail_median"]:
        return "Healthy High-Value"
    if row["total_net_revenue"] > row["_rev_median"] and row["failed_payment_rate"] >= row["_fail_median"]:
        return "High-Value At-Risk"
    if row["recency_days"] > row["_recency_median"]:
        return "Disengaged Low-Value"
    return "Stable Low-Value"


def main():
    full_df = build_customer_features()

    # Customers with zero contracts on file are a distinct, KNOWN category
    # (leads / never-converted), not a behavioral segment worth discovering.
    # Leaving them in dominates the clustering variance (0s vs. everything
    # else) and drowns out the actually interesting structure among real
    # accounts. Handle them as an explicit bucket, cluster the rest.
    has_contract = full_df["contract_count"] > 0
    df = full_df[has_contract].copy()
    no_contract_df = full_df[~has_contract].copy()
    print(f"{len(df)} customers with an active contract on file, "
          f"{len(no_contract_df)} with none (excluded from clustering)")

    X = df[CLUSTER_FEATURES].copy()

    imputer = SimpleImputer(strategy="median")
    X_imputed = imputer.fit_transform(X)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_imputed)

    best_k = choose_k(X_scaled)
    print(f"Selected k={best_k}")

    kmeans = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    df["cluster_id"] = kmeans.fit_predict(X_scaled)

    # Human-readable labels based on where each cluster sits relative to the
    # population median on revenue / risk / recency.
    medians = {
        "_rev_median": df["total_net_revenue"].median(),
        "_fail_median": df["failed_payment_rate"].median(),
        "_recency_median": df["recency_days"].median(),
    }
    cluster_profile = df.groupby("cluster_id")[CLUSTER_FEATURES].mean()
    for k, v in medians.items():
        cluster_profile[k] = v
    cluster_profile["cluster_label"] = cluster_profile.apply(label_cluster, axis=1)

    print("\nCluster profiles (mean feature values):")
    print(cluster_profile[CLUSTER_FEATURES + ["cluster_label"]].round(1).to_string())

    df = df.merge(
        cluster_profile[["cluster_label"]], left_on="cluster_id", right_index=True
    )

    no_contract_df["cluster_id"] = -1
    no_contract_df["cluster_label"] = "No Active Contract"

    all_customers = pd.concat([df, no_contract_df], ignore_index=True)
    segments = all_customers[[
        "customer_id", "cluster_id", "cluster_label", "segment", "region",
        "industry", "total_net_revenue", "recency_days", "failed_payment_rate",
    ]]

    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE SCHEMA IF NOT EXISTS main_ml")
    con.register("segments_tmp", segments)
    con.execute("CREATE OR REPLACE TABLE main_ml.customer_segments AS SELECT * FROM segments_tmp")
    con.unregister("segments_tmp")
    con.close()

    print(f"\nWrote {len(segments)} customers to main_ml.customer_segments")
    print("\nCluster sizes:")
    print(segments["cluster_label"].value_counts())


if __name__ == "__main__":
    main()
