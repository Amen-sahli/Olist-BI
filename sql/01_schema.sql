CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS dw;

-- =========================
-- DIMENSIONS
-- =========================

CREATE TABLE IF NOT EXISTS dw.dim_date (
    date_key        INT PRIMARY KEY,          -- YYYYMMDD
    full_date       DATE NOT NULL UNIQUE,
    year            SMALLINT NOT NULL,
    quarter         SMALLINT NOT NULL,
    month           SMALLINT NOT NULL,
    month_name      VARCHAR(10) NOT NULL,
    year_month      CHAR(7) NOT NULL,         -- '2018-03', handy for Power BI sorting
    week_of_year    SMALLINT NOT NULL,
    day_of_month    SMALLINT NOT NULL,
    day_of_week     SMALLINT NOT NULL,        -- 1 = Monday
    day_name        VARCHAR(10) NOT NULL,
    is_weekend      BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS dw.dim_customer (
    customer_key        SERIAL PRIMARY KEY,
    customer_unique_id  VARCHAR(50) NOT NULL UNIQUE,   -- natural key
    city                VARCHAR(100),
    state               CHAR(2),
    zip_prefix          VARCHAR(10)
);

CREATE TABLE IF NOT EXISTS dw.dim_product (
    product_key             SERIAL PRIMARY KEY,
    product_id              VARCHAR(50) NOT NULL UNIQUE,
    category_name_pt        VARCHAR(100),
    category_name_en        VARCHAR(100) NOT NULL DEFAULT 'unknown',
    weight_g                NUMERIC,
    length_cm               NUMERIC,
    height_cm               NUMERIC,
    width_cm                NUMERIC
);

CREATE TABLE IF NOT EXISTS dw.dim_seller (
    seller_key      SERIAL PRIMARY KEY,
    seller_id       VARCHAR(50) NOT NULL UNIQUE,
    city            VARCHAR(100),
    state           CHAR(2)
);

-- =========================
-- FACTS
-- =========================

-- Grain: one row per order
CREATE TABLE IF NOT EXISTS dw.fact_orders (
    order_id                VARCHAR(50) PRIMARY KEY,
    customer_key            INT NOT NULL REFERENCES dw.dim_customer(customer_key),
    purchase_date_key       INT NOT NULL REFERENCES dw.dim_date(date_key),
    order_status            VARCHAR(20) NOT NULL,

    purchase_ts             TIMESTAMP,
    approved_ts             TIMESTAMP,
    delivered_carrier_ts    TIMESTAMP,
    delivered_customer_ts   TIMESTAMP,
    estimated_delivery_ts   TIMESTAMP,

    items_count             INT,
    items_revenue           NUMERIC(12,2),      -- sum of item prices
    freight_total           NUMERIC(12,2),
    payment_total           NUMERIC(12,2),
    review_score            SMALLINT,           -- deduplicated, NULL if no review

    delivery_days           INT,                -- delivered_customer - purchase
    delivery_delay_days     INT,                -- delivered_customer - estimated (positive = late)
    is_late                 BOOLEAN             -- NULL if not delivered
);

-- Grain: one row per order item
CREATE TABLE IF NOT EXISTS dw.fact_order_items (
    order_id            VARCHAR(50) NOT NULL REFERENCES dw.fact_orders(order_id),
    order_item_id       SMALLINT NOT NULL,
    purchase_date_key   INT NOT NULL REFERENCES dw.dim_date(date_key),
    customer_key        INT NOT NULL REFERENCES dw.dim_customer(customer_key),
    product_key         INT NOT NULL REFERENCES dw.dim_product(product_key),
    seller_key          INT NOT NULL REFERENCES dw.dim_seller(seller_key),
    price               NUMERIC(10,2) NOT NULL,
    freight_value       NUMERIC(10,2) NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);

-- =========================
-- INDEXES (for join and filter speed)
-- =========================
CREATE INDEX IF NOT EXISTS ix_fo_customer ON dw.fact_orders(customer_key);
CREATE INDEX IF NOT EXISTS ix_fo_date     ON dw.fact_orders(purchase_date_key);
CREATE INDEX IF NOT EXISTS ix_foi_product ON dw.fact_order_items(product_key);
CREATE INDEX IF NOT EXISTS ix_foi_seller  ON dw.fact_order_items(seller_key);
CREATE INDEX IF NOT EXISTS ix_foi_date    ON dw.fact_order_items(purchase_date_key);