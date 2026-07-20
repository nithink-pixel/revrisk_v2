{#
  mart_customer_health -- rule-based customer health score.

  DESIGN NOTE: this was built after reviewing an export from a third-party
  tool that claimed to produce a similar score, but ~74% of that export was
  corrupted (literal "Error during cell creation" strings in every column).
  Rather than clone broken logic, this is an original scoring design built
  from data this project actually has and trusts (dbt-tested fct_transactions
  and raw contracts) -- deductions are transparent and documented below, not
  a black box.

  This is DELIBERATELY simpler than mart_revenue_variance_signals: a points-
  deduction system, not a model, because "why is this customer's score 65?"
  needs to be answerable in one sentence to an account owner.

  Grain: one row per customer, CURRENT state (not a monthly time series --
  a genuine monthly history would need point-in-time usage snapshots this
  project doesn't have; this reports health as of the latest data available).

  RELATIONSHIP TO THE PHASE 3 CHURN MODEL (ml/churn_model.py): these are two
  different tools for two different audiences. This mart is instant, free to
  query, and explainable -- right for a live dashboard an account owner reads
  every morning. The ML model is more powerful (ROC-AUC 0.88, learns
  interactions a fixed rule can't) but requires a training/scoring job to
  refresh -- right for prioritizing an outreach list. A real analytics team
  ships both, and explains why they don't always agree.

  SCORING (starts at 100, deducts for each risk factor independently):
    Recency (days since last transaction):
      > 180 days -> -30   ("gone quiet")
      > 90  days -> -15
    Failed payment rate:
      > 15% -> -25
      > 5%  -> -10
    Refund rate:
      > 15% -> -15
      > 5%  -> -5

  primary_risk_driver: whichever single factor contributed the largest
  deduction ("low usage" / "payment failure" / "refund activity"), or
  "stable" if nothing fired.
#}

with transactions as (
    select * from {{ ref('fct_transactions') }}
),

customers as (
    select customer_id, segment, region from {{ ref('dim_customer') }}
),

latest_contract as (
    select
        customer_id,
        contract_value,
        row_number() over (
            partition by customer_id order by contract_start_date desc
        ) as rn
    from {{ source('revrisk_raw', 'contracts') }}
),

as_of as (
    select max(transaction_date) as as_of_date from transactions
),

customer_activity as (
    select
        t.customer_id,
        max(t.transaction_date) as last_transaction_date,
        count(*) as txn_count,
        sum(case when t.payment_status = 'failed' then 1 else 0 end)::float
            / nullif(count(*), 0) as failed_payment_rate,
        sum(case when t.transaction_type in ('refund', 'credit') then 1 else 0 end)::float
            / nullif(count(*), 0) as refund_rate
    from transactions t
    group by 1
),

scored as (
    select
        c.customer_id,
        c.segment,
        c.region,
        coalesce(lc.contract_value, 0) as contract_value,
        a.txn_count,
        (select as_of_date from as_of) - a.last_transaction_date as recency_days,
        coalesce(a.failed_payment_rate, 0) as failed_payment_rate,
        coalesce(a.refund_rate, 0) as refund_rate,

        case
            when a.last_transaction_date is null then 30
            when (select as_of_date from as_of) - a.last_transaction_date > 180 then 30
            when (select as_of_date from as_of) - a.last_transaction_date > 90 then 15
            else 0
        end as recency_deduction,

        case
            when coalesce(a.failed_payment_rate, 0) > 0.15 then 25
            when coalesce(a.failed_payment_rate, 0) > 0.05 then 10
            else 0
        end as failed_payment_deduction,

        case
            when coalesce(a.refund_rate, 0) > 0.15 then 15
            when coalesce(a.refund_rate, 0) > 0.05 then 5
            else 0
        end as refund_deduction

    from customers c
    left join customer_activity a on c.customer_id = a.customer_id
    left join latest_contract lc on c.customer_id = lc.customer_id and lc.rn = 1
)

select
    customer_id,
    segment,
    region,
    greatest(0, 100 - recency_deduction - failed_payment_deduction - refund_deduction) as health_score,

    round(
        (contract_value / 12.0)
        * (recency_deduction + failed_payment_deduction + refund_deduction) / 100.0,
        2
    ) as revenue_at_risk,

    case
        when recency_deduction >= failed_payment_deduction
             and recency_deduction >= refund_deduction
             and recency_deduction > 0
            then 'low usage'
        when failed_payment_deduction >= refund_deduction and failed_payment_deduction > 0
            then 'payment failure'
        when refund_deduction > 0
            then 'refund activity'
        else 'stable'
    end as primary_risk_driver,

    case
        when 100 - recency_deduction - failed_payment_deduction - refund_deduction >= 80 then 'Low'
        when 100 - recency_deduction - failed_payment_deduction - refund_deduction >= 60 then 'Medium'
        else 'High'
    end as risk_level,

    current_timestamp as dbt_loaded_at

from scored
