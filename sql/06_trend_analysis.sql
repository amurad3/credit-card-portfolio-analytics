-- ============================================================================
-- 06_trend_analysis.sql
-- Time-series and transition analysis of delinquency.
--
--   * Monthly portfolio delinquency / charge-off / utilization trend, with the
--     month-over-month change computed via LAG.
--   * Roll rates: the probability an account moves from one delinquency bucket
--     to the next in the following cycle, computed with LEAD over each
--     account's statement history.
--   * Vintage view: current delinquency by account origination year.
--
-- Techniques: window functions (LAG, LEAD, partitioned ordering), CTEs,
-- conditional aggregation.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 6.1  Monthly delinquency trend with month-over-month change (LAG)
-- ----------------------------------------------------------------------------
WITH monthly AS (
    SELECT
        s.statement_month,
        COUNT(*)                                                       AS active_accounts,
        ROUND(COUNT(*) FILTER (WHERE s.days_past_due >= 30)::numeric
              / COUNT(*) * 100, 2)                                     AS delinquency_rate_pct,
        ROUND(COUNT(*) FILTER (WHERE s.charge_off_flag = 1)::numeric
              / COUNT(*) * 100, 2)                                     AS charge_off_rate_pct,
        ROUND(AVG(s.ending_balance / NULLIF(a.credit_limit, 0)) * 100, 1) AS avg_utilization_pct
    FROM monthly_statements s
    JOIN accounts a USING (account_id)
    GROUP BY s.statement_month
)
SELECT
    statement_month,
    active_accounts,
    delinquency_rate_pct,
    delinquency_rate_pct
        - LAG(delinquency_rate_pct) OVER (ORDER BY statement_month)    AS delinquency_mom_change,
    charge_off_rate_pct,
    avg_utilization_pct
FROM monthly
ORDER BY statement_month;


-- ----------------------------------------------------------------------------
-- 6.2  Roll rates: bucket-to-bucket transition probabilities (LEAD)
--      For each statement, look at the same account's NEXT statement and see
--      which delinquency bucket it rolls into.
-- ----------------------------------------------------------------------------
WITH seq AS (
    SELECT
        account_id,
        statement_month,
        days_past_due,
        LEAD(days_past_due) OVER (PARTITION BY account_id
                                  ORDER BY statement_month) AS next_dpd
    FROM monthly_statements
),
classified AS (
    SELECT
        CASE
            WHEN days_past_due = 0   THEN '0 Current'
            WHEN days_past_due < 60  THEN '1 30 DPD'
            WHEN days_past_due < 90  THEN '2 60 DPD'
            ELSE                          '3 90+ DPD'
        END AS from_bucket,
        (next_dpd > days_past_due) AS rolled_forward,
        (next_dpd = 0)             AS cured
    FROM seq
    WHERE next_dpd IS NOT NULL          -- has a following statement
)
SELECT
    from_bucket,
    COUNT(*)                                                       AS observations,
    ROUND(COUNT(*) FILTER (WHERE rolled_forward)::numeric
          / COUNT(*) * 100, 1)                                     AS roll_forward_rate_pct,
    ROUND(COUNT(*) FILTER (WHERE cured)::numeric
          / COUNT(*) * 100, 1)                                     AS cure_rate_pct
FROM classified
GROUP BY from_bucket
ORDER BY from_bucket;


-- ----------------------------------------------------------------------------
-- 6.3  Vintage view: current delinquency by account origination year
--      Newer vintages typically season into higher delinquency.
-- ----------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        s.account_id, s.days_past_due, s.charge_off_flag,
        EXTRACT(YEAR FROM a.open_date)::int AS vintage_year,
        ROW_NUMBER() OVER (PARTITION BY s.account_id
                           ORDER BY s.statement_month DESC) AS rn
    FROM monthly_statements s
    JOIN accounts a USING (account_id)
),
current_state AS (SELECT * FROM ranked WHERE rn = 1)
SELECT
    vintage_year,
    COUNT(*)                                                  AS accounts,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                               AS delinquency_rate_pct,
    ROUND(COUNT(*) FILTER (WHERE charge_off_flag = 1)::numeric
          / COUNT(*) * 100, 2)                               AS charge_off_rate_pct
FROM current_state
GROUP BY vintage_year
ORDER BY vintage_year;
