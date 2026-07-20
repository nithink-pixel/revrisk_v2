{# Staging: rename, cast, standardize. View. #}

with source as (
    select * from {{ source('revrisk_raw', 'contracts') }}
),

renamed as (
    select
        cast(contract_id as varchar)         as contract_id,
        cast(customer_id as varchar)         as customer_id,
        lower(trim(plan))                    as plan,
        cast(contract_start_date as date)    as contract_start_date,
        cast(contract_end_date as date)      as contract_end_date,
        lower(trim(billing_frequency))       as billing_frequency,
        cast(contract_value as decimal(18,2)) as contract_value,
        cast(discount_rate as decimal(9,4))  as discount_rate,
        lower(trim(renewal_status))          as renewal_status,
        cast(account_owner as varchar)       as account_owner
    from source
)

select * from renamed
