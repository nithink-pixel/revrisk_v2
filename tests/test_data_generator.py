"""
Unit tests for the RevRisk synthetic data generator.

These run BEFORE dbt build in CI (see .github/workflows/ci.yml). The point is
to catch generator bugs at the source, cheaply, before they propagate into
dbt models and get caught downstream by (slower, more expensive) dbt tests.

This is also the fix for a CI bug: the workflow's "Python unit tests" step
ran `pytest tests/ -v` against a directory that didn't exist. This file is
that directory's actual contents, not a placeholder.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data_generator"))
import generate  # noqa: E402


def test_customers_have_unique_ids():
    customers = generate.make_customers()
    assert customers["customer_id"].is_unique
    assert len(customers) == generate.N_CUSTOMERS


def test_customers_no_null_ids_or_signup_dates():
    customers = generate.make_customers()
    assert customers["customer_id"].notna().all()
    assert customers["signup_date"].notna().all()


def test_contracts_reference_valid_customers():
    customers = generate.make_customers()
    contracts = generate.make_contracts(customers)
    assert contracts["customer_id"].isin(customers["customer_id"]).all()


def test_contracts_end_date_after_start_date():
    customers = generate.make_customers()
    contracts = generate.make_contracts(customers)
    starts = pd.to_datetime(contracts["contract_start_date"])
    ends = pd.to_datetime(contracts["contract_end_date"])
    assert (ends > starts).all()


def test_contracts_discount_rate_is_a_valid_proportion():
    customers = generate.make_customers()
    contracts = generate.make_contracts(customers)
    assert (contracts["discount_rate"] >= 0).all()
    assert (contracts["discount_rate"] < 1).all()


def test_contracts_renewal_status_is_a_known_value():
    customers = generate.make_customers()
    contracts = generate.make_contracts(customers)
    assert set(contracts["renewal_status"].unique()) <= {"active", "pending", "churned"}


def test_transactions_reference_valid_contracts():
    customers = generate.make_customers()
    contracts = generate.make_contracts(customers)
    transactions = generate.make_transactions(contracts)
    assert transactions["contract_id"].isin(contracts["contract_id"]).all()


def test_transactions_net_amount_never_exceeds_gross_amount():
    # net = gross - discount, and refunds flip net negative but never exceed
    # gross in magnitude given how discount is constructed in the generator.
    customers = generate.make_customers()
    contracts = generate.make_contracts(customers)
    transactions = generate.make_transactions(contracts)
    non_refunds = transactions[transactions["transaction_type"] != "refund"]
    assert (non_refunds["net_amount"] <= non_refunds["gross_amount"]).all()


def test_transactions_no_future_dates():
    customers = generate.make_customers()
    contracts = generate.make_contracts(customers)
    transactions = generate.make_transactions(contracts)
    txn_dates = pd.to_datetime(transactions["transaction_date"])
    assert (txn_dates <= pd.Timestamp(generate.now)).all()


def test_transactions_ids_are_unique():
    customers = generate.make_customers()
    contracts = generate.make_contracts(customers)
    transactions = generate.make_transactions(contracts)
    assert transactions["transaction_id"].is_unique
