-- ============================================================================
-- 03_portfolio_kpis.sql
-- Core portfolio performance KPIs:
--   * Delinquency rate (30+ / 60+ / 90+ days past due)
--   * Credit utilization (balance / credit limit)
--   * Charge-off rate (accounts and balance)
--   * 30 / 60 / 90-day delinquency bucket distribution
--
-- "Current" state is each account's most recent statement, picked with a
-- ROW_NUMBER() window. KPIs are then computed portfolio-wide and sliced by
-- card product and region using FILTER conditional aggregation.
--
-- Techniques: CTEs, ROW_NUMBER() window, conditional aggregation with FILTER,
-- NULLIF-guarded ratios.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 3.1  Portfolio-wide KPIs at the latest statement month
-- ----------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        s.*,
        a.credit_limit,
        ROW_NUMBER() OVER (PARTITION BY s.account_id
                           ORDER BY s.statement_month DESC) AS rn
    FROM monthly_statements s
    JOIN accounts a USING (account_id)
),
current_state AS (
    SELECT
        account_id,
        ending_balance,
        credit_limit,
        days_past_due,
        charge_off_flag,
        ending_balance / NULLIF(credit_limit, 0) AS utilization
    FROM ranked
    WHERE rn = 1
)
SELECT
    COUNT(*)                                                          AS accounts,
    ROUND(SUM(ending_balance), 2)                                    AS total_balance,
    ROUND(AVG(utilization) * 100, 1)                                 AS avg_utilization_pct,
    ROUND(
        SUM(ending_balance) / NULLIF(SUM(credit_limit), 0) * 100, 1) AS portfolio_utilization_pct,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                                       AS delinquency_rate_30plus_pct,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 60)::numeric
          / COUNT(*) * 100, 2)                                       AS delinquency_rate_60plus_pct,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 90)::numeric
          / COUNT(*) * 100, 2)                                       AS delinquency_rate_90plus_pct,
    ROUND(COUNT(*) FILTER (WHERE charge_off_flag = 1)::numeric
          / COUNT(*) * 100, 2)                                       AS charge_off_rate_pct,
    ROUND(SUM(ending_balance) FILTER (WHERE charge_off_flag = 1)
          / NULLIF(SUM(ending_balance), 0) * 100, 2)                 AS charge_off_balance_pct
FROM current_state;


-- ----------------------------------------------------------------------------
-- 3.2  Delinquency bucket distribution (current cycle)
--      Standard 30/60/90/120+ aging buckets via a CASE expression.
-- ----------------------------------------------------------------------------
WITH ranked AS (
    SELECT s.account_id, s.days_past_due, s.charge_off_flag,
           ROW_NUMBER() OVER (PARTITION BY s.account_id
                              ORDER BY s.statement_month DESC) AS rn
    FROM monthly_statements s
),
current_state AS (SELECT * FROM ranked WHERE rn = 1),
bucketed AS (
    SELECT
        CASE
            WHEN charge_off_flag = 1   THEN '6 Charged Off'
            WHEN days_past_due = 0     THEN '1 Current'
            WHEN days_past_due < 60    THEN '2 30 DPD'
            WHEN days_past_due < 90    THEN '3 60 DPD'
            WHEN days_past_due < 120   THEN '4 90 DPD'
            ELSE                            '5 120+ DPD'
        END AS delinquency_bucket
    FROM current_state
)
SELECT
    delinquency_bucket,
    COUNT(*)                                          AS accounts,
    ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER () * 100, 2) AS pct_of_portfolio
FROM bucketed
GROUP BY delinquency_bucket
ORDER BY delinquency_bucket;


-- ----------------------------------------------------------------------------
-- 3.3  KPIs by card product (segment view)
-- ----------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        s.account_id, s.ending_balance, s.days_past_due, s.charge_off_flag,
        a.credit_limit, a.product_id,
        ROW_NUMBER() OVER (PARTITION BY s.account_id
                           ORDER BY s.statement_month DESC) AS rn
    FROM monthly_statements s
    JOIN accounts a USING (account_id)
),
current_state AS (SELECT * FROM ranked WHERE rn = 1)
SELECT
    p.product_name,
    COUNT(*)                                                            AS accounts,
    ROUND(AVG(ending_balance / NULLIF(credit_limit, 0)) * 100, 1)       AS avg_utilization_pct,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                                          AS delinquency_rate_pct,
    ROUND(COUNT(*) FILTER (WHERE charge_off_flag = 1)::numeric
          / COUNT(*) * 100, 2)                                          AS charge_off_rate_pct,
    ROUND(SUM(ending_balance), 2)                                       AS total_balance
FROM current_state c
JOIN card_products p USING (product_id)
GROUP BY p.product_name
ORDER BY delinquency_rate_pct DESC;


-- ----------------------------------------------------------------------------
-- 3.4  KPIs by region
-- ----------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        s.account_id, s.ending_balance, s.days_past_due, s.charge_off_flag,
        a.credit_limit, a.cardholder_id,
        ROW_NUMBER() OVER (PARTITION BY s.account_id
                           ORDER BY s.statement_month DESC) AS rn
    FROM monthly_statements s
    JOIN accounts a USING (account_id)
),
current_state AS (SELECT * FROM ranked WHERE rn = 1)
SELECT
    r.region_name,
    COUNT(*)                                                            AS accounts,
    ROUND(AVG(ending_balance / NULLIF(credit_limit, 0)) * 100, 1)       AS avg_utilization_pct,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                                          AS delinquency_rate_pct,
    ROUND(COUNT(*) FILTER (WHERE charge_off_flag = 1)::numeric
          / COUNT(*) * 100, 2)                                          AS charge_off_rate_pct
FROM current_state c
JOIN cardholders ch USING (cardholder_id)
JOIN regions r USING (region_id)
GROUP BY r.region_name
ORDER BY delinquency_rate_pct DESC;
