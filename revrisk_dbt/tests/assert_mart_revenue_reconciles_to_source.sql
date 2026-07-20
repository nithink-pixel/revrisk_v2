/*
  THE MOST IMPORTANT TEST IN THIS PROJECT.

  Interview story ("tell me about ensuring data quality"):
    Dashboards lose trust the moment two numbers disagree. This test proves the
    revenue number on the executive page is the same number that exists in the
    raw source -- not approximately, exactly. It walks the whole chain:

        raw source  ->  fct_transactions  ->  mart_executive_kpis

    If any layer drops, duplicates, or double-counts a row, this fails and dbt
    stops the build before the dashboard can show a wrong number to an executive.

  A dbt singular test passes when it returns ZERO rows. So this query is written
  to return the mismatches -- rows here mean the test failed.

  Tolerance is 0.01 for float rounding across aggregation layers, not a fudge
  factor. A real discrepancy is never one cent.
*/

with source_revenue as (

    select
        date_trunc('month', transaction_date) as revenue_month,
        sum(net_amount) as source_net_revenue
    from {{ source('revrisk_raw', 'transactions') }}
    group by 1

),

fact_revenue as (

    select
        date_trunc('month', transaction_date) as revenue_month,
        sum(net_amount) as fact_net_revenue
    from {{ ref('fct_transactions') }}
    group by 1

),

mart_revenue as (

    select
        revenue_month,
        sum(net_revenue) as mart_net_revenue
    from {{ ref('mart_executive_kpis') }}
    group by 1

),

reconciliation as (

    select
        s.revenue_month,
        s.source_net_revenue,
        f.fact_net_revenue,
        m.mart_net_revenue,
        abs(s.source_net_revenue - f.fact_net_revenue) as source_to_fact_diff,
        abs(f.fact_net_revenue - m.mart_net_revenue)   as fact_to_mart_diff
    from source_revenue s
    left join fact_revenue f on s.revenue_month = f.revenue_month
    left join mart_revenue  m on s.revenue_month = m.revenue_month

)

select *
from reconciliation
where source_to_fact_diff > 0.01
   or fact_to_mart_diff  > 0.01
   or fact_net_revenue is null
   or mart_net_revenue is null
