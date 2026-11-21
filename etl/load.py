import logging
import pandas as pd
from sqlalchemy import text
from .config import get_engine

log = logging.getLogger(__name__)

# Two categories are missing from Olist's translation file
MANUAL_TRANSLATIONS = {
    "pc_gamer": "pc_gamer",
    "portateis_cozinha_e_preparadores_de_alimentos": "portable_kitchen_food_preparers",
}


# ---------- helpers ----------
def read_stg(engine, table):
    return pd.read_sql(f"SELECT * FROM stg.{table}", engine)


def upsert(engine, df, table, conflict_cols, schema="dw"):
    """Load df into a temp staging table, then INSERT ... ON CONFLICT.
    This is what makes reruns idempotent."""
    tmp = f"_tmp_{table}"
    cols = list(df.columns)
    update_cols = [c for c in cols if c not in conflict_cols]
    if update_cols:
        action = "DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    else:
        action = "DO NOTHING"

    with engine.begin() as conn:
        df.to_sql(tmp, conn, schema="stg", if_exists="replace",
                  index=False, chunksize=5000, method="multi")
        conn.execute(text(f"""
            INSERT INTO {schema}.{table} ({', '.join(cols)})
            SELECT {', '.join(cols)} FROM stg.{tmp}
            ON CONFLICT ({', '.join(conflict_cols)}) {action}
        """))
        conn.execute(text(f"DROP TABLE stg.{tmp}"))
    log.info("dw.%s upserted: %d rows", table, len(df))


# ---------- clean source tables ----------
def clean_orders(engine):
    o = read_stg(engine, "orders")
    ts_map = {
        "order_purchase_timestamp": "purchase_ts",
        "order_approved_at": "approved_ts",
        "order_delivered_carrier_date": "delivered_carrier_ts",
        "order_delivered_customer_date": "delivered_customer_ts",
        "order_estimated_delivery_date": "estimated_delivery_ts",
    }
    for src, dst in ts_map.items():
        o[dst] = pd.to_datetime(o[src])
    return o.drop(columns=list(ts_map)).rename(columns={"order_status": "order_status"})


# ---------- dimensions ----------
def load_dim_date(engine, orders):
    start = orders["purchase_ts"].min().normalize()
    end = orders["purchase_ts"].max().normalize()
    d = pd.DataFrame({"full_date": pd.date_range(start, end, freq="D")})
    fd = d["full_date"]
    d["date_key"] = fd.dt.strftime("%Y%m%d").astype(int)
    d["year"] = fd.dt.year
    d["quarter"] = fd.dt.quarter
    d["month"] = fd.dt.month
    d["month_name"] = fd.dt.month_name()
    d["year_month"] = fd.dt.strftime("%Y-%m")
    d["week_of_year"] = fd.dt.isocalendar().week.astype(int)
    d["day_of_month"] = fd.dt.day
    d["day_of_week"] = fd.dt.dayofweek + 1
    d["day_name"] = fd.dt.day_name()
    d["is_weekend"] = d["day_of_week"] >= 6
    d["full_date"] = fd.dt.date
    d = d[["date_key", "full_date", "year", "quarter", "month", "month_name",
           "year_month", "week_of_year", "day_of_month", "day_of_week",
           "day_name", "is_weekend"]]
    upsert(engine, d, "dim_date", ["date_key"])


def load_dim_customer(engine, orders):
    c = read_stg(engine, "customers")
    # latest known city/state per real person (SCD Type 1)
    m = c.merge(orders[["customer_id", "purchase_ts"]], on="customer_id", how="left")
    m = (m.sort_values("purchase_ts", na_position="first")
          .drop_duplicates("customer_unique_id", keep="last"))
    dim = m.rename(columns={
        "customer_city": "city",
        "customer_state": "state",
        "customer_zip_code_prefix": "zip_prefix",
    })[["customer_unique_id", "city", "state", "zip_prefix"]]
    upsert(engine, dim, "dim_customer", ["customer_unique_id"])


def load_dim_product(engine):
    p = read_stg(engine, "products")
    t = read_stg(engine, "category_translation")
    m = p.merge(t, on="product_category_name", how="left")

    en = m["product_category_name_english"]
    en = en.fillna(m["product_category_name"].map(MANUAL_TRANSLATIONS))
    en = en.fillna(m["product_category_name"])   # fallback: keep Portuguese name
    en = en.fillna("unknown")                    # null category

    dim = pd.DataFrame({
        "product_id": m["product_id"],
        "category_name_pt": m["product_category_name"],
        "category_name_en": en,
        "weight_g": pd.to_numeric(m["product_weight_g"]),
        "length_cm": pd.to_numeric(m["product_length_cm"]),
        "height_cm": pd.to_numeric(m["product_height_cm"]),
        "width_cm": pd.to_numeric(m["product_width_cm"]),
    })
    upsert(engine, dim, "dim_product", ["product_id"])


def load_dim_seller(engine):
    s = read_stg(engine, "sellers")
    dim = s.rename(columns={"seller_city": "city", "seller_state": "state"})[
        ["seller_id", "city", "state"]]
    upsert(engine, dim, "dim_seller", ["seller_id"])


# ---------- facts ----------
def load_fact_orders(engine, orders):
    items = read_stg(engine, "order_items")
    items["price"] = pd.to_numeric(items["price"])
    items["freight_value"] = pd.to_numeric(items["freight_value"])
    item_agg = items.groupby("order_id").agg(
        items_count=("order_item_id", "size"),
        items_revenue=("price", "sum"),
        freight_total=("freight_value", "sum"),
    ).reset_index()

    pay = read_stg(engine, "order_payments")
    pay["payment_value"] = pd.to_numeric(pay["payment_value"])
    pay_agg = (pay.groupby("order_id")["payment_value"].sum()
                  .rename("payment_total").reset_index())

    # Deduplicate reviews: keep the most recent review per order
    rev = read_stg(engine, "order_reviews")
    rev["review_score"] = pd.to_numeric(rev["review_score"])
    rev["review_answer_ts"] = pd.to_datetime(rev["review_answer_timestamp"])
    rev = (rev.sort_values("review_answer_ts")
              .drop_duplicates("order_id", keep="last")[["order_id", "review_score"]])

    cust = read_stg(engine, "customers")[["customer_id", "customer_unique_id"]]
    dim_c = pd.read_sql("SELECT customer_key, customer_unique_id FROM dw.dim_customer", engine)

    f = (orders.merge(cust, on="customer_id", how="left")
               .merge(dim_c, on="customer_unique_id", how="left")
               .merge(item_agg, on="order_id", how="left")
               .merge(pay_agg, on="order_id", how="left")
               .merge(rev, on="order_id", how="left"))

    f[["items_count", "items_revenue", "freight_total"]] = \
        f[["items_count", "items_revenue", "freight_total"]].fillna(0)

    f["purchase_date_key"] = f["purchase_ts"].dt.strftime("%Y%m%d").astype(int)

    # Derived delivery metrics (NULL when the order was not delivered)
    delivered = f["delivered_customer_ts"].notna()
    f["delivery_days"] = (f["delivered_customer_ts"] - f["purchase_ts"]).dt.days.astype("Int64")
    f["delivery_delay_days"] = (f["delivered_customer_ts"] - f["estimated_delivery_ts"]).dt.days.astype("Int64")
    late = (f["delivered_customer_ts"] > f["estimated_delivery_ts"]).astype("boolean")
    late[~delivered] = pd.NA
    f["is_late"] = late
    f["review_score"] = f["review_score"].astype("Int64")
    f["items_count"] = f["items_count"].astype(int)

    cols = ["order_id", "customer_key", "purchase_date_key", "order_status",
            "purchase_ts", "approved_ts", "delivered_carrier_ts",
            "delivered_customer_ts", "estimated_delivery_ts",
            "items_count", "items_revenue", "freight_total", "payment_total",
            "review_score", "delivery_days", "delivery_delay_days", "is_late"]
    f = f[cols]
    if f["customer_key"].isna().any():
        raise ValueError("Orders found without a matching customer_key")
    upsert(engine, f, "fact_orders", ["order_id"])


def load_fact_order_items(engine, orders):
    items = read_stg(engine, "order_items")
    items["order_item_id"] = items["order_item_id"].astype(int)
    items["price"] = pd.to_numeric(items["price"])
    items["freight_value"] = pd.to_numeric(items["freight_value"])

    cust = read_stg(engine, "customers")[["customer_id", "customer_unique_id"]]
    dim_c = pd.read_sql("SELECT customer_key, customer_unique_id FROM dw.dim_customer", engine)
    dim_p = pd.read_sql("SELECT product_key, product_id FROM dw.dim_product", engine)
    dim_s = pd.read_sql("SELECT seller_key, seller_id FROM dw.dim_seller", engine)

    o = (orders[["order_id", "customer_id", "purchase_ts"]]
         .merge(cust, on="customer_id", how="left")
         .merge(dim_c, on="customer_unique_id", how="left"))
    o["purchase_date_key"] = o["purchase_ts"].dt.strftime("%Y%m%d").astype(int)

    f = (items.merge(o[["order_id", "customer_key", "purchase_date_key"]], on="order_id", how="left")
              .merge(dim_p, on="product_id", how="left")
              .merge(dim_s, on="seller_id", how="left"))

    key_cols = ["customer_key", "product_key", "seller_key", "purchase_date_key"]
    if f[key_cols].isna().any().any():
        raise ValueError("Order items with unresolved dimension keys")

    f = f[["order_id", "order_item_id", "purchase_date_key", "customer_key",
           "product_key", "seller_key", "price", "freight_value"]]
    upsert(engine, f, "fact_order_items", ["order_id", "order_item_id"])


def load_all(engine):
    orders = clean_orders(engine)
    load_dim_date(engine, orders)
    load_dim_customer(engine, orders)
    load_dim_product(engine)
    load_dim_seller(engine)
    load_fact_orders(engine, orders)
    load_fact_order_items(engine, orders)