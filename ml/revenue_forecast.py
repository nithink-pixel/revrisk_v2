"""
Phase 3 -- revenue forecasting.

Only 19 months of history exist in this dataset (2025-01 through 2026-07),
which is too short to reliably fit a seasonal model -- claiming to detect
yearly seasonality from 1.5 cycles of data would be dishonest. Holt's linear
trend method (double exponential smoothing, no seasonal component) is the
right-sized tool here: it captures trend and recent momentum without
overclaiming.

Run:
    python ml/revenue_forecast.py
"""

from pathlib import Path

import duckdb
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from feature_engineering import DB_PATH

FORECAST_MONTHS = 6
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_DIR.mkdir(exist_ok=True)


def load_monthly_revenue() -> pd.Series:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.execute("""
        select revenue_month, sum(net_revenue) as net_revenue
        from main_analytics.mart_executive_kpis
        group by 1
        order by 1
    """).df()
    con.close()
    series = df.set_index("revenue_month")["net_revenue"]
    series.index = pd.DatetimeIndex(series.index, freq="MS")
    return series


def main():
    full_series = load_monthly_revenue()
    print(f"Loaded {len(full_series)} months of revenue: {full_series.index.min().date()} to {full_series.index.max().date()}")

    if len(full_series) < 8:
        raise ValueError("Not enough history to forecast responsibly (need 8+ months).")

    # The most recent calendar month is almost always partial (data generation
    # or ingestion cuts off mid-month), and including it in a trend fit reads
    # as a sudden collapse that isn't real. Drop it from the FIT, but keep it
    # in the historical output for transparency.
    series = full_series.iloc[:-1]
    print(f"Excluding partial current month ({full_series.index[-1].date()}) from the trend fit.")

    model = ExponentialSmoothing(series, trend="add", damped_trend=True, seasonal=None)
    fit = model.fit()

    forecast = fit.forecast(FORECAST_MONTHS)
    forecast_dates = pd.date_range(
        series.index.max() + pd.DateOffset(months=1), periods=FORECAST_MONTHS, freq="MS"
    )

    # In-sample fit error, honestly reported -- this is what "how good is your
    # forecast" should point to, not a made-up confidence interval.
    fitted = fit.fittedvalues
    mape = (abs(series - fitted) / series).mean() * 100
    print(f"In-sample MAPE: {mape:.1f}%")

    result = pd.DataFrame({
        "month": forecast_dates,
        "forecast_net_revenue": forecast.values,
        "is_forecast": True,
    })
    historical = pd.DataFrame({
        "month": full_series.index,
        "forecast_net_revenue": full_series.values,
        "is_forecast": False,
    })
    combined = pd.concat([historical, result], ignore_index=True)
    combined["model_mape_pct"] = round(mape, 2)

    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE SCHEMA IF NOT EXISTS main_ml")
    con.register("forecast_tmp", combined)
    con.execute("CREATE OR REPLACE TABLE main_ml.revenue_forecast AS SELECT * FROM forecast_tmp")
    con.unregister("forecast_tmp")
    con.close()

    print(f"\nForecast (next {FORECAST_MONTHS} months):")
    print(result.to_string(index=False))
    print(f"\nWrote {len(combined)} rows to main_ml.revenue_forecast (history + forecast)")


if __name__ == "__main__":
    main()
