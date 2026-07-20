"""
Phase 3 -- churn prediction model.

Trains a classifier on customer_id-grain features (see feature_engineering.py),
evaluates it honestly (stratified split, AUC + precision/recall, not just
accuracy on a 6.6%-positive-class problem where "always predict no churn"
already scores 93% accuracy), and writes scores back to the warehouse as
main_ml.churn_scores -- the table the dashboard (Phase 5) will read from.

Run:
    python ml/churn_model.py
"""

import json
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from feature_engineering import (
    FEATURE_COLUMNS_CATEGORICAL,
    FEATURE_COLUMNS_NUMERIC,
    DB_PATH,
    build_customer_features,
)

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_DIR.mkdir(exist_ok=True)
RANDOM_STATE = 42


def build_preprocessor() -> ColumnTransformer:
    numeric_pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical_pipe = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore")),
    ])
    return ColumnTransformer([
        ("num", numeric_pipe, FEATURE_COLUMNS_NUMERIC),
        ("cat", categorical_pipe, FEATURE_COLUMNS_CATEGORICAL),
    ])


def evaluate(model, X_test, y_test, name: str) -> dict:
    proba = model.predict_proba(X_test)[:, 1]
    preds = (proba >= 0.5).astype(int)
    auc = roc_auc_score(y_test, proba)
    ap = average_precision_score(y_test, proba)
    print(f"\n--- {name} ---")
    print(f"ROC-AUC:            {auc:.3f}")
    print(f"Avg Precision (PR):  {ap:.3f}")
    print(classification_report(y_test, preds, target_names=["retained", "churned"]))
    return {"model": name, "roc_auc": auc, "avg_precision": ap}


def main():
    print("Building features from the dbt-built warehouse...")
    df = build_customer_features()
    print(f"{len(df)} customers, churn rate {df['is_churned'].mean():.1%}")

    X = df[FEATURE_COLUMNS_NUMERIC + FEATURE_COLUMNS_CATEGORICAL]
    y = df["is_churned"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=RANDOM_STATE, stratify=y
    )

    preprocessor = build_preprocessor()

    candidates = {
        "logistic_regression": LogisticRegression(
            max_iter=1000, class_weight="balanced", random_state=RANDOM_STATE
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=300, max_depth=6, class_weight="balanced",
            random_state=RANDOM_STATE,
        ),
        "xgboost": XGBClassifier(
            n_estimators=300, max_depth=4, learning_rate=0.05,
            # imbalance ratio, not class_weight -- xgboost's native handle
            scale_pos_weight=(y_train == 0).sum() / max((y_train == 1).sum(), 1),
            eval_metric="logloss", random_state=RANDOM_STATE,
        ),
    }

    results = []
    fitted = {}
    for name, clf in candidates.items():
        pipe = Pipeline([("preprocess", preprocessor), ("model", clf)])
        pipe.fit(X_train, y_train)
        fitted[name] = pipe
        results.append(evaluate(pipe, X_test, y_test, name))

    results_df = pd.DataFrame(results).sort_values("roc_auc", ascending=False)
    print("\n=== Model comparison (sorted by ROC-AUC) ===")
    print(results_df.to_string(index=False))

    best_name = results_df.iloc[0]["model"]
    best_pipe = fitted[best_name]
    print(f"\nSelected model: {best_name}")

    # Refit best model on ALL labeled data before scoring the full population --
    # standard practice once model selection is done via the held-out test set.
    best_pipe.fit(X, y)

    # Feature importance (works for RF/XGB natively; logistic uses coefficients)
    feature_names = (
        FEATURE_COLUMNS_NUMERIC
        + list(
            best_pipe.named_steps["preprocess"]
            .named_transformers_["cat"]
            .named_steps["onehot"]
            .get_feature_names_out(FEATURE_COLUMNS_CATEGORICAL)
        )
    )
    model_step = best_pipe.named_steps["model"]
    if hasattr(model_step, "feature_importances_"):
        importances = model_step.feature_importances_
    else:
        importances = np.abs(model_step.coef_[0])
    importance_df = (
        pd.DataFrame({"feature": feature_names, "importance": importances})
        .sort_values("importance", ascending=False)
        .head(15)
    )
    print("\nTop features:")
    print(importance_df.to_string(index=False))

    # Score the full population
    df["churn_probability"] = best_pipe.predict_proba(X)[:, 1]
    df["risk_tier"] = pd.cut(
        df["churn_probability"],
        bins=[-0.01, 0.25, 0.5, 0.75, 1.01],
        labels=["low", "medium", "high", "critical"],
    )

    scored = df[[
        "customer_id", "churn_probability", "risk_tier", "is_churned",
        "segment", "region", "industry", "total_net_revenue",
    ]].rename(columns={"is_churned": "currently_churned"})

    # Persist artifacts
    joblib.dump(best_pipe, ARTIFACT_DIR / "churn_model.joblib")
    with open(ARTIFACT_DIR / "churn_model_metrics.json", "w") as f:
        json.dump({
            "selected_model": best_name,
            "comparison": results_df.to_dict(orient="records"),
            "top_features": importance_df.to_dict(orient="records"),
            "n_customers_scored": len(scored),
        }, f, indent=2, default=str)

    # Write scores back to the warehouse -- this is what makes it "deployed",
    # not just a notebook exercise. Phase 5 (dashboard) reads this table.
    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE SCHEMA IF NOT EXISTS main_ml")
    con.register("scored_tmp", scored)
    con.execute("CREATE OR REPLACE TABLE main_ml.churn_scores AS SELECT * FROM scored_tmp")
    con.unregister("scored_tmp")
    con.close()

    print(f"\nWrote {len(scored)} scored customers to main_ml.churn_scores")
    print(f"Model artifact: {ARTIFACT_DIR / 'churn_model.joblib'}")
    print(f"\nRisk tier distribution:\n{scored['risk_tier'].value_counts()}")


if __name__ == "__main__":
    main()
