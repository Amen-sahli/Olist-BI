import pandas as pd
import pytest
from etl.config import get_engine, RAW

INVALID = ["canceled", "unavailable"]


@pytest.fixture(scope="module")
def engine():
    return get_engine()


@pytest.fixture(scope="module")
def raw():
    orders = pd.read_csv(
        RAW / "olist_orders_dataset.csv",
        parse_dates=["order_purchase_timestamp",
                     "order_delivered_customer_date",
                     "order_estimated_delivery_date"],
    )
    items = pd.read_csv(RAW / "olist_order_items_dataset.csv")
    customers = pd.read_csv(RAW / "olist_customers_dataset.csv")
    return orders, items, customers


def test_monthly_revenue_and_aov(engine, raw):
    orders, items, _ = raw
    valid = orders[~orders.order_status.isin(INVALID)]
    m = items.merge(valid[["order_id", "order_purchase_timestamp"]], on="order_id")
    m["year_month"] = m.order_purchase_timestamp.dt.strftime("%Y-%m")
    exp = (m.groupby("year_month")
             .agg(revenue=("price", "sum"), orders=("order_id", "nunique"))
             .reset_index())
    exp["aov"] = exp.revenue / exp.orders

    got = pd.read_sql("SELECT year_month, revenue, orders, aov "
                      "FROM dw.v_kpi_monthly_revenue", engine)
    got[["revenue", "aov"]] = got[["revenue", "aov"]].astype(float)

    merged = exp.merge(got, on="year_month", suffixes=("_pd", "_sql"))
    assert len(merged) == len(exp) == len(got)
    assert (merged.revenue_pd - merged.revenue_sql).abs().max() < 0.01
    assert (merged.orders_pd - merged.orders_sql).abs().max() == 0
    assert (merged.aov_pd - merged.aov_sql).abs().max() < 0.01


def test_late_delivery_by_state(engine, raw):
    orders, _, customers = raw
    # dim_customer is SCD Type 1: state = latest known state per real person
    c = customers.merge(orders[["customer_id", "order_purchase_timestamp"]],
                        on="customer_id", how="left")
    latest = (c.sort_values("order_purchase_timestamp", na_position="first")
                .drop_duplicates("customer_unique_id", keep="last")
                [["customer_unique_id", "customer_state"]])

    d = orders[(orders.order_status == "delivered")
               & orders.order_delivered_customer_date.notna()]
    d = (d.merge(customers[["customer_id", "customer_unique_id"]], on="customer_id")
          .merge(latest, on="customer_unique_id"))
    d["late"] = d.order_delivered_customer_date > d.order_estimated_delivery_date
    exp = (d.groupby("customer_state")
             .agg(delivered=("order_id", "size"), pct_late=("late", "mean"))
             .reset_index())
    exp["pct_late"] *= 100

    got = pd.read_sql("SELECT state, delivered_orders, pct_late "
                      "FROM dw.v_kpi_delivery_by_state", engine)
    got["pct_late"] = got.pct_late.astype(float)

    merged = exp.merge(got, left_on="customer_state", right_on="state")
    assert len(merged) == len(exp) == len(got)
    assert (merged.delivered - merged.delivered_orders).abs().max() == 0
    assert (merged.pct_late_x - merged.pct_late_y).abs().max() < 0.01


def test_repeat_rate_and_cohorts(engine, raw):
    orders, _, customers = raw
    valid = orders[~orders.order_status.isin(INVALID)]
    v = valid.merge(customers[["customer_id", "customer_unique_id"]], on="customer_id")
    per_customer = v.groupby("customer_unique_id").size()

    got = pd.read_sql("SELECT customers, repeat_customers FROM dw.v_kpi_repeat_rate", engine)
    assert got.customers[0] == len(per_customer)
    assert got.repeat_customers[0] == (per_customer > 1).sum()

    cohort_total = pd.read_sql(
        "SELECT SUM(active_customers) AS n FROM dw.v_kpi_cohort_retention "
        "WHERE months_since = 0", engine).n[0]
    assert cohort_total == len(per_customer)