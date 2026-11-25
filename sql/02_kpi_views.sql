-- Base: valid orders only
CREATE OR REPLACE VIEW dw.v_valid_orders AS
SELECT *
FROM dw.fact_orders
WHERE order_status NOT IN ('canceled', 'unavailable');

-- 1) Revenue trend + AOV + month-over-month growth
CREATE OR REPLACE VIEW dw.v_kpi_monthly_revenue AS
WITH m AS (
    SELECT d.year_month,
           SUM(i.price)                AS revenue,
           COUNT(DISTINCT i.order_id)  AS orders
    FROM dw.fact_order_items i
    JOIN dw.v_valid_orders o USING (order_id)
    JOIN dw.dim_date d ON d.date_key = i.purchase_date_key
    GROUP BY d.year_month
)
SELECT year_month,
       revenue,
       orders,
       ROUND(revenue / orders, 2) AS aov,
       ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY year_month))
             / NULLIF(LAG(revenue) OVER (ORDER BY year_month), 0), 1) AS mom_growth_pct
FROM m
ORDER BY year_month;

-- 2) Top categories
CREATE OR REPLACE VIEW dw.v_kpi_top_categories AS
SELECT p.category_name_en                         AS category,
       SUM(i.price)                               AS revenue,
       COUNT(DISTINCT i.order_id)                 AS orders,
       COUNT(*)                                   AS items_sold,
       ROUND(100.0 * SUM(i.price) / SUM(SUM(i.price)) OVER (), 2) AS revenue_share_pct
FROM dw.fact_order_items i
JOIN dw.v_valid_orders o USING (order_id)
JOIN dw.dim_product p USING (product_key)
GROUP BY p.category_name_en
ORDER BY revenue DESC;

-- 3) Delivery performance by customer state
CREATE OR REPLACE VIEW dw.v_kpi_delivery_by_state AS
SELECT c.state,
       COUNT(*)                                              AS delivered_orders,
       ROUND(100.0 * AVG(o.is_late::int), 2)                 AS pct_late,
       ROUND(AVG(o.delivery_days), 1)                        AS avg_delivery_days,
       ROUND(AVG(o.delivery_delay_days) FILTER (WHERE o.is_late), 1) AS avg_days_late
FROM dw.fact_orders o
JOIN dw.dim_customer c USING (customer_key)
WHERE o.order_status = 'delivered'
  AND o.delivered_customer_ts IS NOT NULL
GROUP BY c.state
ORDER BY pct_late DESC;

-- 4) Review score: late vs on time (your "insight" KPI)
CREATE OR REPLACE VIEW dw.v_kpi_review_vs_delay AS
SELECT CASE WHEN is_late THEN 'Late' ELSE 'On time' END AS delivery_status,
       COUNT(*)                          AS orders,
       ROUND(AVG(review_score), 2)       AS avg_review_score
FROM dw.fact_orders
WHERE order_status = 'delivered'
  AND is_late IS NOT NULL
  AND review_score IS NOT NULL
GROUP BY 1;

-- 5) Repeat-customer rate
CREATE OR REPLACE VIEW dw.v_kpi_repeat_rate AS
SELECT COUNT(*)                                   AS customers,
       COUNT(*) FILTER (WHERE n_orders > 1)       AS repeat_customers,
       ROUND(100.0 * COUNT(*) FILTER (WHERE n_orders > 1) / COUNT(*), 2) AS repeat_rate_pct
FROM (
    SELECT customer_key, COUNT(*) AS n_orders
    FROM dw.v_valid_orders
    GROUP BY customer_key
) t;

-- 6) Cohort retention
CREATE OR REPLACE VIEW dw.v_kpi_cohort_retention AS
WITH purchases AS (
    SELECT customer_key,
           DATE_TRUNC('month', purchase_ts)::date AS order_month
    FROM dw.v_valid_orders
    GROUP BY 1, 2
),
first_orders AS (
    SELECT customer_key, MIN(order_month) AS cohort_month
    FROM purchases
    GROUP BY 1
),
activity AS (
    SELECT f.cohort_month,
           (EXTRACT(YEAR  FROM AGE(p.order_month, f.cohort_month)) * 12
          + EXTRACT(MONTH FROM AGE(p.order_month, f.cohort_month)))::int AS months_since,
           COUNT(DISTINCT p.customer_key) AS active_customers
    FROM purchases p
    JOIN first_orders f USING (customer_key)
    GROUP BY 1, 2
)
SELECT a.cohort_month,
       a.months_since,
       a.active_customers,
       s.cohort_size,
       ROUND(100.0 * a.active_customers / s.cohort_size, 2) AS retention_pct
FROM activity a
JOIN (SELECT cohort_month, active_customers AS cohort_size
      FROM activity WHERE months_since = 0) s USING (cohort_month)
ORDER BY a.cohort_month, a.months_since;