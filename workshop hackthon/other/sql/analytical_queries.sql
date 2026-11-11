-- =============================================================================
-- analytical_queries.sql
-- Business questions answered from the star schema.
--
-- Conventions
--   * "Recognised revenue" excludes CANCELLED and RETURNED orders. Order status
--     analysis (query 10) shows all statuses.
--   * Facts are at order-line grain; every join to a dimension is many-to-one on
--     a surrogate key, so joins never multiply rows (no double counting).
--   * Rankings of products / customers group by the BUSINESS key so that SCD2
--     versions of one product/customer are not split into several rows; the
--     current name is shown. Geography / category use the HISTORICAL attribute
--     valid at the time of sale (that is the point of SCD Type 2).
--   * Average order value aggregates to order level first, then averages.
--
-- Each query starts with "-- name: <id>" so the CLI can run it:
--     python -m src.cli queries --name monthly_revenue
-- =============================================================================

-- name: total_revenue
-- 1. Total recognised revenue
SELECT count(DISTINCT f.order_id)  AS orders,
       count(*)                    AS order_lines,
       sum(f.quantity)             AS units_sold,
       sum(f.gross_sales)          AS gross_sales,
       sum(f.discount_amount)      AS discounts,
       sum(f.net_revenue)          AS net_revenue
FROM dw.fact_sales f
WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED');

-- name: monthly_revenue
-- 2. Monthly revenue with month-over-month growth
WITH monthly AS (
    SELECT d.year, d.month, d.month_name,
           count(DISTINCT f.order_id) AS orders,
           sum(f.net_revenue)         AS net_revenue
    FROM dw.fact_sales f
    JOIN dw.dim_date d ON d.date_key = f.date_key
    WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
    GROUP BY d.year, d.month, d.month_name
)
SELECT year, month, month_name, orders, net_revenue,
       round(100.0 * (net_revenue - lag(net_revenue) OVER (ORDER BY year, month))
             / NULLIF(lag(net_revenue) OVER (ORDER BY year, month), 0), 2) AS mom_growth_pct
FROM monthly
ORDER BY year, month;

-- name: yearly_revenue
-- 3. Yearly revenue with year-over-year growth
WITH yearly AS (
    SELECT d.year,
           count(DISTINCT f.order_id) AS orders,
           sum(f.quantity)            AS units_sold,
           sum(f.net_revenue)         AS net_revenue
    FROM dw.fact_sales f
    JOIN dw.dim_date d ON d.date_key = f.date_key
    WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
    GROUP BY d.year
)
SELECT year, orders, units_sold, net_revenue,
       round(100.0 * (net_revenue - lag(net_revenue) OVER (ORDER BY year))
             / NULLIF(lag(net_revenue) OVER (ORDER BY year), 0), 2) AS yoy_growth_pct
FROM yearly
ORDER BY year;

-- name: top_10_products
-- 4. Top 10 best-selling products (by units, revenue as tie-breaker)
SELECT p.product_id,
       cur.product_name,
       cur.category,
       sum(f.quantity)            AS units_sold,
       count(DISTINCT f.order_id) AS orders,
       sum(f.net_revenue)         AS net_revenue
FROM dw.fact_sales f
JOIN dw.dim_product p   ON p.product_key = f.product_key
JOIN dw.dim_product cur ON cur.product_id = p.product_id AND cur.is_current
WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
GROUP BY p.product_id, cur.product_name, cur.category
ORDER BY units_sold DESC, net_revenue DESC, p.product_id
LIMIT 10;

-- name: category_revenue
-- 5. Revenue by product category (category valid at time of sale)
SELECT p.category,
       count(DISTINCT f.order_id) AS orders,
       sum(f.quantity)            AS units_sold,
       sum(f.net_revenue)         AS net_revenue,
       round(100.0 * sum(f.net_revenue) / sum(sum(f.net_revenue)) OVER (), 2) AS revenue_share_pct
FROM dw.fact_sales f
JOIN dw.dim_product p ON p.product_key = f.product_key
WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
GROUP BY p.category
ORDER BY net_revenue DESC, p.category;

-- name: city_revenue
-- 6. Revenue by customer city (city where the customer lived at time of sale)
SELECT c.city,
       c.state,
       count(DISTINCT c.customer_id) AS customers,
       count(DISTINCT f.order_id)    AS orders,
       sum(f.net_revenue)            AS net_revenue
FROM dw.fact_sales f
JOIN dw.dim_customer c ON c.customer_key = f.customer_key
WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
GROUP BY c.city, c.state
ORDER BY net_revenue DESC, c.city;

-- name: top_10_customers
-- 7. Top 10 customers by spending (all versions of a customer combined)
SELECT c.customer_id,
       cur.first_name || COALESCE(' ' || cur.last_name, '') AS customer_name,
       cur.city                                             AS current_city,
       count(DISTINCT f.order_id)                           AS orders,
       sum(f.net_revenue)                                   AS total_spent
FROM dw.fact_sales f
JOIN dw.dim_customer c   ON c.customer_key = f.customer_key
JOIN dw.dim_customer cur ON cur.customer_id = c.customer_id AND cur.is_current
WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
GROUP BY c.customer_id, cur.first_name, cur.last_name, cur.city
ORDER BY total_spent DESC, c.customer_id
LIMIT 10;

-- name: average_order_value
-- 8. Average order value - aggregate to ORDER level first, then average
WITH order_totals AS (
    SELECT f.order_id, sum(f.net_revenue) AS order_value
    FROM dw.fact_sales f
    WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
    GROUP BY f.order_id
)
SELECT count(*)                          AS orders,
       round(avg(order_value), 2)        AS average_order_value,
       min(order_value)                  AS min_order_value,
       max(order_value)                  AS max_order_value,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY order_value)::numeric(14,2) AS median_order_value
FROM order_totals;

-- name: total_quantity_sold
-- 9. Total quantity sold (overall and by category)
SELECT COALESCE(p.category, 'ALL CATEGORIES') AS category,
       sum(f.quantity)                        AS units_sold
FROM dw.fact_sales f
JOIN dw.dim_product p ON p.product_key = f.product_key
WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
GROUP BY ROLLUP (p.category)
ORDER BY (p.category IS NULL) DESC, units_sold DESC;

-- name: order_status_analysis
-- 10. Order status analysis (all statuses; status is an order-level attribute)
WITH orders AS (
    SELECT f.order_id, f.order_status, sum(f.net_revenue) AS order_value, sum(f.quantity) AS units
    FROM dw.fact_sales f
    GROUP BY f.order_id, f.order_status
)
SELECT order_status,
       count(*)                                           AS orders,
       round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct_of_orders,
       sum(units)                                         AS units,
       sum(order_value)                                   AS order_value
FROM orders
GROUP BY order_status
ORDER BY orders DESC, order_status;

-- name: revenue_by_payment_method
-- 11. Revenue by payment method
WITH orders AS (
    SELECT f.order_id, f.payment_method, sum(f.net_revenue) AS order_value
    FROM dw.fact_sales f
    WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
    GROUP BY f.order_id, f.payment_method
)
SELECT payment_method,
       count(*)                                                   AS orders,
       sum(order_value)                                           AS net_revenue,
       round(avg(order_value), 2)                                 AS average_order_value,
       round(100.0 * sum(order_value) / sum(sum(order_value)) OVER (), 2) AS revenue_share_pct
FROM orders
GROUP BY payment_method
ORDER BY net_revenue DESC, payment_method;

-- name: historical_customer_sales
-- 12. Historical customer analysis: sales per customer VERSION (SCD2).
--     Shows that sales made before a move stay with the old city.
SELECT c.customer_id,
       c.customer_key,
       c.city,
       c.state,
       c.effective_start_date,
       c.effective_end_date,
       c.is_current,
       count(f.sales_key)                 AS order_lines,
       COALESCE(sum(f.net_revenue), 0)    AS net_revenue
FROM dw.dim_customer c
LEFT JOIN dw.fact_sales f
       ON f.customer_key = c.customer_key
      AND f.order_status NOT IN ('CANCELLED', 'RETURNED')
WHERE c.customer_id IN (SELECT customer_id FROM dw.dim_customer
                        GROUP BY customer_id HAVING count(*) > 1)
GROUP BY c.customer_id, c.customer_key, c.city, c.state, c.effective_start_date,
         c.effective_end_date, c.is_current
ORDER BY c.customer_id, c.effective_start_date;

-- name: customer_purchase_behaviour
-- 13. Customer purchasing behaviour: one-time vs repeat buyers
WITH per_customer AS (
    SELECT c.customer_id, count(DISTINCT f.order_id) AS orders, sum(f.net_revenue) AS spent
    FROM dw.fact_sales f
    JOIN dw.dim_customer c ON c.customer_key = f.customer_key
    WHERE f.order_status NOT IN ('CANCELLED', 'RETURNED')
    GROUP BY c.customer_id
)
SELECT CASE WHEN orders = 1 THEN '1 order'
            WHEN orders BETWEEN 2 AND 3 THEN '2-3 orders'
            WHEN orders BETWEEN 4 AND 9 THEN '4-9 orders'
            ELSE '10+ orders' END            AS segment,
       count(*)                              AS customers,
       sum(orders)                           AS orders,
       sum(spent)                            AS net_revenue,
       round(avg(spent), 2)                  AS avg_spend_per_customer
FROM per_customer
GROUP BY 1
ORDER BY min(orders);
