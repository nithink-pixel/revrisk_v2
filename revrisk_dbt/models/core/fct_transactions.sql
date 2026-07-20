{#
  fct_transactions -- the incremental fact.
  ADJUST COLUMN NAMES to match your actual RevRisk source. The structure and the
  reasoning are what matter; the column list is a placeholder.

  ===================================================================
  THE FIVE QUESTIONS AN INTERVIEWER WILL ASK ABOUT THIS MODEL
  ===================================================================

  1. "What is the grain?"
     One row per transaction. transaction_id is unique across the table. Every
     measure here (gross, discount, net) is additive at that grain, which is why
     revenue rolls up correctly to customer, month, and region without fan-out.

  2. "Why is it incremental?"
     60K+ transactions and growing. A full rebuild reprocesses history that will
     never change. Incremental means each run only touches rows newer than what's
     already loaded -- seconds instead of a full scan, and on Snowflake that is
     a direct compute-cost decision, not just a speed one.

  3. "How do you handle late-arriving records?"
     The filter uses a lookback window, not a hard max(). If I filtered on
     transaction_date > (select max(transaction_date) from this), a transaction
     that lands three days late would be silently dropped forever. The 3-day
     window reprocesses recent history so late arrivals get picked up, and the
     merge strategy overwrites rather than duplicates them.

  4. "How do you prevent duplicates?"
     unique_key + merge. dbt matches on transaction_id and updates the existing
     row instead of inserting a second one. This is what makes the lookback window
     safe -- reprocessing the same 3 days is idempotent.

  5. "When do you full-refresh?"
     When business logic changes (a new derived column, a fixed calculation),
     because incremental only applies logic to new rows. Old rows keep the old
     logic until rebuilt: dbt build --full-refresh --select fct_transactions.
  ===================================================================
#}

{{
  config(
    materialized='incremental',
    unique_key='transaction_id',
    incremental_strategy='merge',
    on_schema_change='append_new_columns'
  )
}}

with transactions as (

    select * from {{ ref('stg_transactions') }}

    {% if is_incremental() %}
    -- Lookback window, not max(). See rationale #3 above.
    where transaction_date >= (
        select coalesce(max(transaction_date), '1900-01-01'::date) - interval '3 days'
        from {{ this }}
    )
    {% endif %}

),

enriched as (

    select
        t.transaction_id,
        t.customer_id,
        t.contract_id,
        t.transaction_date,
        t.transaction_type,

        -- Measures. Additive at this grain.
        t.gross_amount,
        t.discount_amount,
        t.net_amount,

        -- Derived: discount as a rate, guarded against divide-by-zero.
        case
            when t.gross_amount > 0
            then t.discount_amount / t.gross_amount
            else 0
        end as discount_rate,

        t.payment_status,

        -- Degenerate dimension: invoice_id lives on the fact because an invoice
        -- has no attributes of its own worth a dimension table.
        t.invoice_id,

        -- Surrogate FK to dim_date.
        -- Portable across DuckDB and Snowflake: to_char() is Snowflake-only and
        -- strftime() is DuckDB-only, so neither can appear in a warehouse-portable
        -- model. Integer arithmetic on extracted parts works identically on both.
        (extract(year  from t.transaction_date) * 10000
       + extract(month from t.transaction_date) * 100
       + extract(day   from t.transaction_date))::integer as date_key,

        current_timestamp as dbt_loaded_at

    from transactions t

)

select * from enriched
