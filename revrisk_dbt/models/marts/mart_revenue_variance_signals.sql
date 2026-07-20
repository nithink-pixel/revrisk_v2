{#
  mart_revenue_variance_signals -- month-over-month revenue anomaly detection.

  Grain: one row per region x segment x month, comparing net_revenue to the
  SAME region+segment in the PRIOR month. This is deliberately simple --
  month-over-month, not a statistical control chart -- because it's
  explainable to an executive in one sentence, which matters more for a
  signal that's going to show up on a dashboard than a more "sophisticated"
  model nobody can explain when asked why it fired.

  SEVERITY: only declines get flagged as High; growth is never a severity
  concern here (estimated_revenue_impact is 0 for any positive variance --
  the "impact" this table tracks is money AT RISK, not money gained).
    - High:  decline of 25% or more month-over-month
    - Low:   everything else (including all growth, and small declines)

  This mirrors mart_executive_kpis' governance philosophy: one place defines
  what "notable revenue change" means, so the dashboard and any ad-hoc
  analysis agree on it.
#}

{% set decline_severity_threshold = -0.25 %}

with monthly as (
    select
        revenue_month,
        region,
        segment,
        net_revenue
    from {{ ref('mart_executive_kpis') }}
),

with_prior_month as (
    select
        *,
        lag(net_revenue) over (
            partition by region, segment order by revenue_month
        ) as prior_month_net_revenue
    from monthly
),

variance as (
    select
        region,
        segment,
        revenue_month,
        net_revenue as current_value,
        prior_month_net_revenue as baseline_value,
        net_revenue - prior_month_net_revenue as absolute_variance,
        case
            when prior_month_net_revenue is null or prior_month_net_revenue = 0 then null
            else (net_revenue - prior_month_net_revenue) / prior_month_net_revenue
        end as percentage_variance
    from with_prior_month
    -- first month per region/segment has no baseline -- nothing to compare, not a signal
    where prior_month_net_revenue is not null
      -- the most recent calendar month is typically partial (see
      -- ml/revenue_forecast.py for the same issue and reasoning) -- including
      -- it reads as a sudden collapse that isn't real, so it's excluded as a
      -- CURRENT-month comparison. It still exists in mart_executive_kpis and
      -- will be compared correctly once a later month makes it a complete
      -- historical period.
      and revenue_month < (select max(revenue_month) from monthly)
)

select
    'REV-' || cast(revenue_month as varchar) || '-' || region || ':' || segment as signal_id,
    revenue_month as signal_date,
    'revenue_variance' as signal_type,
    'segment' as entity_type,
    region || ':' || segment as entity_id,
    'net_revenue' as metric_name,
    current_value,
    baseline_value,
    absolute_variance,
    percentage_variance,
    case
        when percentage_variance <= {{ decline_severity_threshold }} then 'High'
        else 'Low'
    end as severity,
    -- flat confidence: this is a rule, not a statistical model, so there's no
    -- learned uncertainty to report -- 0.85 documents "trust this, but a
    -- human should still look," not a computed probability.
    0.85 as confidence,
    case
        when absolute_variance < 0 then abs(absolute_variance)
        else 0
    end as estimated_revenue_impact,
    'month-over-month variance' as primary_driver,
    'review revenue and refund activity' as recommended_action,
    'open' as signal_status,
    'mart_executive_kpis' as source_model,
    current_timestamp as created_at
from variance
