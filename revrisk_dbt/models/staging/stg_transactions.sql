{#
  Staging: rename, cast, standardize. No business logic, no joins.
  Materialized as a view -- cheap, and I never want a stale copy of source data.
  One staging model per source table. This is the only layer allowed to call source().
#}

with source as (

    select * from {{ source('revrisk_raw', 'transactions') }}

),

renamed as (

    select
        cast(transaction_id as varchar) as transaction_id,
        cast(customer_id    as varchar) as customer_id,
        cast(contract_id    as varchar) as contract_id,
        cast(invoice_id     as varchar) as invoice_id,

        cast(transaction_date as date) as transaction_date,

        -- standardize enum casing at the boundary so nothing downstream must
        lower(trim(transaction_type)) as transaction_type,
        lower(trim(payment_status))   as payment_status,

        -- coalesce nulls to 0 so sums don't silently vanish
        coalesce(cast(gross_amount    as decimal(18,2)), 0) as gross_amount,
        coalesce(cast(discount_amount as decimal(18,2)), 0) as discount_amount,
        coalesce(cast(net_amount      as decimal(18,2)), 0) as net_amount

    from source

)

select * from renamed
