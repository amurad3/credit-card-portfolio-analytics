-- ============================================================================
-- 04_risk_segmentation.sql
-- Cardholder risk segmentation.
--
-- Builds a reusable view of each account's CURRENT state (latest statement)
-- enriched with utilization, a thin-file flag, and a rule-based risk tier. The
-- tier is built from origination/behavioral risk signals (FICO band, current
-- utilization, thin credit file) and deliberately does NOT use days-past-due,
-- so "delinquency rate by tier" downstream is a genuine validation of the
-- segmentation rather than a tautology.
--
-- The same tier definition is reproduced in python/analytics.py, so the SQL and
-- Python layers report identical segment populations.
--
-- Techniques: CREATE VIEW, multi-CTE, ordered CASE scoring, NTILE() quantiles,
-- conditional aggregation with FILTER.
-- ============================================================================

DROP VIEW IF EXISTS v_account_current;

CREATE VIEW v_account_current AS
WITH ranked AS (
    SELECT
        s.account_id,
        s.statement_month,
        s.ending_balance,
        s.days_past_due,
        s.charge_off_flag,
        a.credit_limit,
        a.product_id,
        a.cardholder_id,
        ROW_NUMBER() OVER (PARTITION BY s.account_id
                           ORDER BY s.statement_month DESC) AS rn
    FROM monthly_statements s
    JOIN accounts a USING (account_id)
),
current_state AS (
    SELECT
        r.account_id,
        r.cardholder_id,
        r.product_id,
        r.ending_balance,
        r.credit_limit,
        r.days_past_due,
        r.charge_off_flag,
        r.ending_balance / NULLIF(r.credit_limit, 0) AS utilization,
        ch.fico_origination,
        ch.credit_history_months,
        ch.annual_income,
        ch.region_id,
        (ch.credit_history_months < 24) AS thin_file
    FROM ranked r
    JOIN cardholders ch USING (cardholder_id)
    WHERE r.rn = 1
),
scored AS (
    SELECT
        cs.*,
        -- Risk points: FICO band + utilization band + thin-file penalty.
        ( CASE WHEN fico_origination < 660 THEN 2
               WHEN fico_origination < 720 THEN 1 ELSE 0 END
        + CASE WHEN utilization >= 0.90 THEN 2
               WHEN utilization >= 0.60 THEN 1 ELSE 0 END
        + CASE WHEN thin_file THEN 1 ELSE 0 END ) AS risk_points
    FROM current_state cs
)
SELECT
    *,
    CASE
        WHEN risk_points >= 4 THEN 'Tier 4 - High Risk'
        WHEN risk_points = 3  THEN 'Tier 3 - Elevated'
        WHEN risk_points >= 1 THEN 'Tier 2 - Moderate'
        ELSE                       'Tier 1 - Low Risk'
    END AS risk_tier
FROM scored;


-- ----------------------------------------------------------------------------
-- 4.1  Portfolio KPIs by risk tier (the segmentation centerpiece)
--      Delinquency and charge-off should rise monotonically across tiers if the
--      segmentation is meaningful.
-- ----------------------------------------------------------------------------
SELECT
    risk_tier,
    COUNT(*)                                                          AS accounts,
    ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER () * 100, 1)         AS pct_of_accounts,
    ROUND(AVG(fico_origination))                                     AS avg_fico,
    ROUND(AVG(utilization) * 100, 1)                                 AS avg_utilization_pct,
    ROUND(AVG(CASE WHEN thin_file THEN 1 ELSE 0 END) * 100, 1)       AS pct_thin_file,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                                       AS delinquency_rate_pct,
    ROUND(COUNT(*) FILTER (WHERE charge_off_flag = 1)::numeric
          / COUNT(*) * 100, 2)                                       AS charge_off_rate_pct,
    ROUND(SUM(ending_balance), 2)                                    AS total_balance,
    ROUND(SUM(ending_balance) / SUM(SUM(ending_balance)) OVER () * 100, 1) AS pct_of_balance
FROM v_account_current
GROUP BY risk_tier
ORDER BY risk_tier;


-- ----------------------------------------------------------------------------
-- 4.2  Risk concentration: share of total balance and of charged-off balance
--      held by each tier. Shows whether risk (and loss) is concentrated.
-- ----------------------------------------------------------------------------
SELECT
    risk_tier,
    ROUND(SUM(ending_balance) / SUM(SUM(ending_balance)) OVER () * 100, 1)  AS pct_total_balance,
    ROUND(
        SUM(ending_balance) FILTER (WHERE charge_off_flag = 1)
        / NULLIF(SUM(SUM(ending_balance) FILTER (WHERE charge_off_flag = 1)) OVER (), 0)
        * 100, 1)                                                          AS pct_charged_off_balance
FROM v_account_current
GROUP BY risk_tier
ORDER BY risk_tier;


-- ----------------------------------------------------------------------------
-- 4.3  Continuous risk quantiles with NTILE: split the book into 10 deciles by
--      a simple risk score (high utilization, low FICO) and show the
--      delinquency gradient across deciles.
-- ----------------------------------------------------------------------------
WITH scored AS (
    SELECT
        account_id,
        days_past_due,
        utilization,
        fico_origination,
        -- Higher score = riskier: high utilization, low FICO (scaled to ~0-1).
        (0.6 * LEAST(utilization, 1.0) + 0.4 * (850 - fico_origination) / 550.0) AS risk_score
    FROM v_account_current
),
deciles AS (
    SELECT *, NTILE(10) OVER (ORDER BY risk_score) AS risk_decile
    FROM scored
)
SELECT
    risk_decile,
    COUNT(*)                                                  AS accounts,
    ROUND(AVG(utilization) * 100, 1)                          AS avg_utilization_pct,
    ROUND(AVG(fico_origination))                             AS avg_fico,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                               AS delinquency_rate_pct
FROM deciles
GROUP BY risk_decile
ORDER BY risk_decile;
