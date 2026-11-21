import logging
from sqlalchemy import text

log = logging.getLogger(__name__)

CHECKS = [
    ("fact_orders row count matches staging",
     "SELECT (SELECT COUNT(*) FROM dw.fact_orders) = (SELECT COUNT(*) FROM stg.orders)"),
    ("fact_order_items row count matches staging",
     "SELECT (SELECT COUNT(*) FROM dw.fact_order_items) = (SELECT COUNT(*) FROM stg.order_items)"),
    ("item revenue matches staging",
     "SELECT ROUND((SELECT SUM(price) FROM dw.fact_order_items)::numeric, 2) = "
     "ROUND((SELECT SUM(price::numeric) FROM stg.order_items), 2)"),
    ("review scores between 1 and 5",
     "SELECT COUNT(*) = 0 FROM dw.fact_orders WHERE review_score NOT BETWEEN 1 AND 5"),
    ("delivered orders have is_late set",
     "SELECT COUNT(*) = 0 FROM dw.fact_orders WHERE delivered_customer_ts IS NOT NULL AND is_late IS NULL"),
    ("no duplicate customer_unique_id",
     "SELECT COUNT(*) = COUNT(DISTINCT customer_unique_id) FROM dw.dim_customer"),
    ("no 'unknown' category above 2% of items",
     "SELECT COALESCE(AVG((p.category_name_en = 'unknown')::int), 0) < 0.02 "
     "FROM dw.fact_order_items i JOIN dw.dim_product p USING (product_key)"),
]


def run_checks(engine):
    failed = 0
    with engine.connect() as conn:
        for name, sql in CHECKS:
            ok = conn.execute(text(sql)).scalar()
            log.info("%s  %s", "PASS" if ok else "FAIL", name)
            failed += 0 if ok else 1
    if failed:
        raise RuntimeError(f"{failed} data-quality check(s) failed")