{#
  Date dimension. Grain: one row per calendar day.

  Why a date dimension rather than just using transaction_date:
    Every time-based question (month-over-month, quarter, weekday vs weekend,
    fiscal period) becomes a join instead of a scattered set of date functions
    re-implemented in each model. It is the single most reused dimension in the
    warehouse, and it makes date_key joins fast.
#}

{#
  Date spine. NOTE ON PORTABILITY: generating a date series is one of the few
  places SQL dialects genuinely diverge -- DuckDB has range(), Snowflake needs
  generator(). Rather than pretend one syntax covers both, this branches on
  target.type explicitly. Everything downstream stays dialect-neutral.
#}

with date_spine as (

    {% if target.type == 'snowflake' %}
    select dateadd(day, seq4(), '2023-01-01'::date) as date_day
    from table(generator(rowcount => 1826))
    {% else %}
    select cast(range as date) as date_day
    from range(date '2023-01-01', date '2027-12-31', interval 1 day)
    {% endif %}

)

select
    (extract(year from date_day) * 10000
   + extract(month from date_day) * 100
   + extract(day from date_day))::integer as date_key,
    date_day,
    extract(year    from date_day) as year,
    extract(quarter from date_day) as quarter,
    extract(month   from date_day) as month,

    extract(day     from date_day) as day_of_month,
    extract(dayofweek from date_day) as day_of_week,

    case when extract(dayofweek from date_day) in (0, 6) then true else false end as is_weekend,
    date_trunc('month',   date_day) as month_start,
    date_trunc('quarter', date_day) as quarter_start
from date_spine
