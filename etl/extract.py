import logging
import pandas as pd
from .config import RAW

log = logging.getLogger(__name__)

FILES = {
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


def extract(engine):
    for table, filename in FILES.items():
        df = pd.read_csv(RAW / filename, dtype=str)
        df.to_sql(table, engine, schema="stg", if_exists="replace",
                  index=False, chunksize=5000, method="multi")
        log.info("stg.%s loaded: %d rows", table, len(df))