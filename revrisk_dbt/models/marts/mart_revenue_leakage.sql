{#
  mart_revenue_leakage -- transaction-grain leakage detail.

  WHY THIS EXISTS: mart_executive_kpis tells you WHAT net revenue is. This
  tells you WHERE money is being left on the table, at the transaction level,
  so account owners can act on individual deals instead of a single aggregate
  number going down.

  Grain: one row per transaction that has leakage of at least one kind.
  Transactions with zero leakage are excluded on purpose -- this is a
  leakage REPORT, not a full transaction dump; mart_executive_kpis and
  fct_transactions already serve that purpose.

  THREE LEAKAGE TYPES, priority order when a transaction qualifies for more
  than one (refund > discount > failed_payment, reflecting revenue-impact
  severity -- a refund is money already gone, a failed payment might still
  be recovered):

    1. discount_leakage: gross_amount * (discount_rate - 0.15), when the
       effective discount exceeds a 15% target rate. 0.15 is the same
       benchmark used elsewhere in this project's effective_discount_rate
       commentary -- discounting beyond it is treated as leakage, not
       strategy.
    2. refund_leakage: the full absolute value of a refund/credit transaction.
    3. failed_payment_leakage: the full gross_amount of any transaction with
       payment_status = 'failed' -- money invoiced but not yet collected.
#}

{% set discount_benchmark_rate = 0.15 %}

with transactions as (
    select * from {{ ref('fct_transactions') }}
),

customers as (
    select customer_id, segment, region from {{ ref('dim_customer') }}
),

leakage_calc as (
    select
        t.transaction_id,
        t.customer_id,
        t.contract_id,
        t.transaction_date,
        c.region,
        c.segment,
        t.transaction_type,
        t.payment_status,
        t.gross_amount,
        t.discount_amount,
        t.net_amount,

        greatest(
            0,
            t.gross_amount * (t.discount_rate - {{ discount_benchmark_rate }})
        ) as estimated_discount_leakage,

        case
            when t.transaction_type in ('refund', 'credit') then abs(t.net_amount)
            else 0
        end as refund_leakage,

        case
            when t.payment_status = 'failed' then t.gross_amount
            else 0
        end as failed_payment_leakage

    from transactions t
    left join customers c on t.customer_id = c.customer_id
),

typed as (
    select
        *,
        case
            when refund_leakage > 0 then 'refund'
            when estimated_discount_leakage > 0 then 'discount'
            when failed_payment_leakage > 0 then 'failed_payment'
            else null
        end as leakage_type
    from leakage_calc
)

select *
from typed
where estimated_discount_leakage > 0
   or refund_leakage > 0
   or failed_payment_leakage > 0
