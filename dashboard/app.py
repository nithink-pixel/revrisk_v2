"""
RevRisk Intelligence Dashboard -- Phase 5.

Reads ONLY from the dbt-built warehouse (main_analytics, main_ml schemas) --
never from raw sources, and never recomputes a metric here that's already
governed in dbt. If a number needs to change, it changes in the dbt model
that owns it, not in this file. That's the whole point of mart_executive_kpis
existing: one definition of net_revenue, read by this dashboard, any ad-hoc
query, and the reconciliation test alike.

Run:
    streamlit run dashboard/app.py
"""

from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "revrisk_dev.duckdb"

st.set_page_config(page_title="RevRisk Intelligence", layout="wide", page_icon="📊")


@st.cache_resource
def get_connection():
    return duckdb.connect(str(DB_PATH), read_only=True)


def query(sql: str) -> pd.DataFrame | None:
    """Runs a query; returns None (with an inline notice) instead of crashing
    the whole page if a table doesn't exist yet -- e.g. the main_ml schema
    only exists after the Phase 3 ML scripts have been run at least once."""
    try:
        return get_connection().execute(sql).df()
    except duckdb.CatalogException:
        st.info(
            "This section needs a table that hasn't been built yet. "
            "Run `dbt build` and the scripts in `ml/` first."
        )
        return None


st.title("📊 RevRisk Intelligence Dashboard")
st.caption(
    "Reads directly from the dbt-built DuckDB warehouse. "
    "Every number here traces back to a tested, documented dbt model."
)

tab_overview, tab_leakage, tab_health, tab_segments, tab_anomalies, tab_forecast = st.tabs([
    "Executive Overview", "Revenue Leakage", "Customer Health & Churn",
    "Segments", "Anomalies", "Forecast",
])

# ---------------------------------------------------------------------------
# Executive Overview
# ---------------------------------------------------------------------------
with tab_overview:
    kpi = query("""
        select revenue_month, region, segment, net_revenue, gross_revenue,
               discount_amount, effective_discount_rate, failed_payment_value,
               active_customers
        from main_analytics.mart_executive_kpis
        order by revenue_month
    """)

    if kpi is not None and len(kpi):
        monthly = kpi.groupby("revenue_month", as_index=False).agg(
            net_revenue=("net_revenue", "sum"),
            gross_revenue=("gross_revenue", "sum"),
            failed_payment_value=("failed_payment_value", "sum"),
            active_customers=("active_customers", "sum"),
        )
        # exclude the partial current month from headline totals -- same
        # reasoning as ml/revenue_forecast.py and mart_revenue_variance_signals
        complete_months = monthly.iloc[:-1] if len(monthly) > 1 else monthly
        latest = complete_months.iloc[-1]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Net Revenue (latest complete month)", f"${latest['net_revenue']:,.0f}")
        c2.metric("Active Customers", f"{int(latest['active_customers']):,}")
        avg_discount = kpi["effective_discount_rate"].mean()
        c3.metric("Avg Effective Discount Rate", f"{avg_discount:.1%}")
        c4.metric("Failed Payment Value (latest month)", f"${latest['failed_payment_value']:,.0f}")

        st.plotly_chart(
            px.line(monthly, x="revenue_month", y="net_revenue", markers=True,
                     title="Net Revenue by Month"),
            width='stretch',
        )

        col_a, col_b = st.columns(2)
        with col_a:
            by_region = kpi.groupby("region", as_index=False)["net_revenue"].sum()
            st.plotly_chart(
                px.bar(by_region, x="region", y="net_revenue", title="Net Revenue by Region"),
                width='stretch',
            )
        with col_b:
            by_segment = kpi.groupby("segment", as_index=False)["net_revenue"].sum()
            st.plotly_chart(
                px.pie(by_segment, names="segment", values="net_revenue", title="Revenue Mix by Segment"),
                width='stretch',
            )

# ---------------------------------------------------------------------------
# Revenue Leakage
# ---------------------------------------------------------------------------
with tab_leakage:
    leakage = query("""
        select * from main_analytics.mart_revenue_leakage
    """)
    if leakage is not None and len(leakage):
        total_leakage = (
            leakage["estimated_discount_leakage"]
            + leakage["refund_leakage"]
            + leakage["failed_payment_leakage"]
        ).sum()

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Estimated Leakage", f"${total_leakage:,.0f}")
        c2.metric("Flagged Transactions", f"{len(leakage):,}")
        c3.metric(
            "Largest Category",
            leakage["leakage_type"].value_counts().idxmax().replace("_", " ").title(),
        )

        by_type = leakage.groupby("leakage_type", as_index=False).apply(
            lambda g: pd.Series({
                "total_leakage": (
                    g["estimated_discount_leakage"].sum()
                    + g["refund_leakage"].sum()
                    + g["failed_payment_leakage"].sum()
                )
            }),
            include_groups=False,
        )
        by_type["leakage_type"] = leakage.groupby("leakage_type").size().index
        st.plotly_chart(
            px.bar(by_type, x="leakage_type", y="total_leakage", title="Leakage by Type"),
            width='stretch',
        )

        st.subheader("Top leaking transactions")
        top = leakage.copy()
        top["total_leakage"] = (
            top["estimated_discount_leakage"] + top["refund_leakage"] + top["failed_payment_leakage"]
        )
        st.dataframe(
            top.sort_values("total_leakage", ascending=False)
            [["transaction_id", "customer_id", "leakage_type", "total_leakage", "transaction_date"]]
            .head(25),
            use_container_width=True, hide_index=True,
        )

    st.subheader("Revenue variance signals")
    signals = query("""
        select * from main_analytics.mart_revenue_variance_signals
        where severity = 'High'
        order by signal_date desc
    """)
    if signals is not None and len(signals):
        st.dataframe(
            signals[["entity_id", "signal_date", "current_value", "baseline_value",
                     "percentage_variance", "estimated_revenue_impact"]],
            use_container_width=True, hide_index=True,
        )
    elif signals is not None:
        st.success("No High-severity revenue variance signals currently open.")

# ---------------------------------------------------------------------------
# Customer Health & Churn
# ---------------------------------------------------------------------------
with tab_health:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Rule-based health score (mart_customer_health)")
        health = query("select * from main_analytics.mart_customer_health")
        if health is not None and len(health):
            st.plotly_chart(
                px.histogram(health, x="health_score", nbins=20, title="Health Score Distribution"),
                width='stretch',
            )
            risk_counts = health["risk_level"].value_counts().reset_index()
            risk_counts.columns = ["risk_level", "count"]
            st.plotly_chart(
                px.bar(risk_counts, x="risk_level", y="count",
                       category_orders={"risk_level": ["Low", "Medium", "High"]},
                       title="Customers by Risk Level"),
                width='stretch',
            )

    with col2:
        st.subheader("ML churn model (ml/churn_model.py)")
        churn = query("select * from main_ml.churn_scores")
        if churn is not None and len(churn):
            tier_counts = churn["risk_tier"].value_counts().reset_index()
            tier_counts.columns = ["risk_tier", "count"]
            st.plotly_chart(
                px.bar(tier_counts, x="risk_tier", y="count",
                       category_orders={"risk_tier": ["low", "medium", "high", "critical"]},
                       title="Customers by Churn Risk Tier"),
                width='stretch',
            )
            st.caption(
                "Rule-based score (left) and ML model (right) are two different lenses -- "
                "see ml/churn_model.py for why a real analytics team ships both."
            )

    if health is not None and churn is not None:
        st.subheader("Highest revenue at risk (top 25)")
        combined = health.merge(
            churn[["customer_id", "churn_probability", "risk_tier"]],
            on="customer_id", how="left",
        )
        st.dataframe(
            combined.sort_values("revenue_at_risk", ascending=False)
            [["customer_id", "segment", "region", "health_score", "risk_level",
              "churn_probability", "risk_tier", "revenue_at_risk"]]
            .head(25),
            use_container_width=True, hide_index=True,
        )

# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------
with tab_segments:
    segments = query("select * from main_ml.customer_segments")
    if segments is not None and len(segments):
        c1, c2 = st.columns(2)
        with c1:
            counts = segments["cluster_label"].value_counts().reset_index()
            counts.columns = ["cluster_label", "count"]
            st.plotly_chart(
                px.pie(counts, names="cluster_label", values="count", title="Customers by Segment"),
                width='stretch',
            )
        with c2:
            revenue_by_cluster = segments.groupby("cluster_label", as_index=False)["total_net_revenue"].sum()
            st.plotly_chart(
                px.bar(revenue_by_cluster, x="cluster_label", y="total_net_revenue",
                       title="Revenue by Segment"),
                width='stretch',
            )
        st.dataframe(segments, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------
with tab_anomalies:
    anomalies = query("""
        select * from main_ml.transaction_anomalies where anomaly_flag = true
        order by anomaly_score desc
    """)
    if anomalies is not None and len(anomalies):
        c1, c2 = st.columns(2)
        c1.metric("Flagged Transactions", f"{len(anomalies):,}")
        c2.metric("Highest Anomaly Score", f"{anomalies['anomaly_score'].max():.3f}")
        st.dataframe(
            anomalies[["transaction_id", "customer_id", "transaction_type", "gross_amount",
                       "discount_rate", "payment_status", "anomaly_score"]].head(50),
            use_container_width=True, hide_index=True,
        )

# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------
with tab_forecast:
    forecast = query("select * from main_ml.revenue_forecast order by month")
    if forecast is not None and len(forecast):
        fig = px.line(
            forecast, x="month", y="forecast_net_revenue", color="is_forecast",
            markers=True, title="Net Revenue: History vs. Forecast",
        )
        st.plotly_chart(fig, width='stretch')
        mape = forecast["model_mape_pct"].dropna().iloc[0] if "model_mape_pct" in forecast else None
        if mape is not None:
            st.caption(f"In-sample MAPE: {mape:.1f}% (Holt's linear trend, partial current month excluded from the fit)")
