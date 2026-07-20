{#
  Customer dimension. Grain: one row per customer_id.

  Interview note: this is Type 1 (overwrite) on purpose. Customer attributes like
  industry and region are descriptive, not economic -- if a customer's region is
  corrected, I want every historical report to show the corrected region, because
  the old value was simply wrong. Contract terms are different: those changed for
  real business reasons and history must be preserved, which is why contracts get
  an SCD Type 2 snapshot and customers do not.

  Table materialization: small, joined by every fact, changes rarely.
#}

with customers as (
    select * from {{ ref('stg_customers') }}
)

select
    customer_id,
    customer_name,
    industry,
    segment,
    region,
    account_owner,
    signup_date,

    -- derived attribute: tenure band for cohort slicing
    case
        when date_diff('day', signup_date, current_date) < 180 then 'new'
        when date_diff('day', signup_date, current_date) < 365 then 'established'
        else 'mature'
    end as tenure_band,

    current_timestamp as dbt_loaded_at

from customers
