{#
  Intermediate: reusable business logic, referenced by multiple marts.
  Ephemeral -- it's inlined as a CTE into whatever selects it, so it never
  materializes a table nobody queries directly.
#}

with transactions as (
    select * from {{ ref('fct_transactions') }}
),

customers as (
    select * from {{ ref('dim_customer') }}
)

select
    t.transaction_id,
    t.customer_id,
    t.contract_id,
    t.transaction_date,
    t.transaction_type,
    t.gross_amount,
    t.discount_amount,
    t.net_amount,
    t.discount_rate,
    t.payment_status,

    c.segment,
    c.region,
    c.industry,
    c.account_owner,

    date_trunc('month', t.transaction_date) as revenue_month,

    -- revenue classification, defined once here rather than in every mart
    case
        when t.transaction_type in ('subscription', 'renewal') then 'recurring'
        when t.transaction_type = 'expansion'                  then 'expansion'
        when t.transaction_type in ('refund', 'credit')        then 'contra'
        else 'other'
    end as revenue_category

from transactions t
left join customers c on t.customer_id = c.customer_id
