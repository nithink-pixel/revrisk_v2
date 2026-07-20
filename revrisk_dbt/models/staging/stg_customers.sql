{# Staging: rename, cast, standardize. View. No joins, no business logic. #}

with source as (
    select * from {{ source('revrisk_raw', 'customers') }}
),

renamed as (
    select
        cast(customer_id as varchar)   as customer_id,
        cast(customer_name as varchar) as customer_name,
        lower(trim(industry))          as industry,
        lower(trim(segment))           as segment,
        lower(trim(region))            as region,
        cast(signup_date as date)      as signup_date,
        cast(account_owner as varchar) as account_owner
    from source
)

select * from renamed
