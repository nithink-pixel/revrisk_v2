{#
  Governed KPI mart. The ONLY place net_revenue is defined for the executive page.

  Interview answer for "why not calculate this in Streamlit?":
    Because then the number exists in two places and drifts. Defining it once in
    dbt means the dashboard, any ad-hoc query, and the reconciliation test all
    read the same definition. That is what makes the number trustworthy -- and
    the reconciliation test proves it matches source.
#}

with enriched as (
    select * from {{ ref('int_transactions_enriched') }}
)

select
    revenue_month,
    region,
    segment,

    -- === GOVERNED METRIC DEFINITIONS ===
    -- net_revenue: sum of net_amount, all transaction types. Refunds are already
    -- negative at source, so they subtract naturally. Grain: month x region x segment.
    sum(net_amount)                                                   as net_revenue,
    sum(gross_amount)                                                 as gross_revenue,
    sum(discount_amount)                                              as discount_amount,
    sum(case when revenue_category = 'recurring' then net_amount else 0 end) as recurring_revenue,
    sum(case when revenue_category = 'expansion' then net_amount else 0 end) as expansion_revenue,
    sum(case when transaction_type = 'refund'    then net_amount else 0 end) as refund_amount,

    count(distinct customer_id)                                       as active_customers,
    count(*)                                                          as transaction_count,

    -- discount_rate: weighted, not an average of rates. Averaging rates gives every
    -- transaction equal weight regardless of size, which misstates the real cost.
    case
        when sum(gross_amount) > 0
        then sum(discount_amount) / sum(gross_amount)
        else 0
    end                                                               as effective_discount_rate,

    sum(case when payment_status = 'failed' then gross_amount else 0 end) as failed_payment_value,

    current_timestamp                                                 as dbt_loaded_at

from enriched
group by 1, 2, 3
