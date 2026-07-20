{#
  SCD Type 2 on contracts.

  WHY THIS EXISTS (the interview question you will get):
    "Why can't you just overwrite the contract record?"

    Because reporting has to reflect what was true at the time. If a customer's
    contract value drops from $200K to $120K in March, and you overwrite the row,
    then February's revenue-at-risk report silently changes the next time it runs.
    Your historicals stop reconciling and nobody trusts the dashboard.

    A snapshot keeps a new row per change with dbt_valid_from / dbt_valid_to, so
    fct_customer_monthly can join to the contract version that was active in that
    month, not the version that happens to be current today.

  STRATEGY:
    check, not timestamp -- the source has no reliable updated_at column. check
    compares the listed columns and writes a new version when any of them differ.
    If the source later gains a trustworthy updated_at, timestamp strategy is
    cheaper because it doesn't diff every column.

  COLUMNS TRACKED: only the ones that change the economics of the contract.
    Tracking every column would create version churn on fields nobody reports on.
#}

{% snapshot snap_contracts %}

{{
  config(
    target_schema='snapshots',
    unique_key='contract_id',
    strategy='check',
    check_cols=[
      'plan',
      'contract_value',
      'discount_rate',
      'renewal_status',
      'account_owner',
      'contract_end_date'
    ],
    invalidate_hard_deletes=True
  )
}}

select
    contract_id,
    customer_id,
    plan,
    contract_start_date,
    contract_end_date,
    billing_frequency,
    contract_value,
    discount_rate,
    renewal_status,
    account_owner
from {{ source('revrisk_raw', 'contracts') }}

{% endsnapshot %}
