-- ============================================================================
-- 05_root_cause.sql
-- Root-cause analysis of delinquency.
--
-- Goal: explain WHY delinquency is elevated by isolating the segments that drive
-- it. The analysis decomposes the current-cycle delinquency rate by utilization
-- band and by credit-file depth, then shows their interaction and quantifies how
-- much of total delinquency is concentrated in the high-utilization / thin-file
-- segments.
--
-- Depends on the v_account_current view created in 04_risk_segmentation.sql.
--
-- Techniques: CTEs, CASE banding, conditional aggregation with FILTER, window
-- functions for share-of-total, GROUPING SETS for the interaction grid.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 5.1  Delinquency rate by utilization band
-- ----------------------------------------------------------------------------
WITH banded AS (
    SELECT
        CASE
            WHEN utilization < 0.30 THEN '1 <30%'
            WHEN utilization < 0.60 THEN '2 30-60%'
            WHEN utilization < 0.90 THEN '3 60-90%'
            ELSE                         '4 90%+'
        END AS utilization_band,
        days_past_due,
        ending_balance
    FROM v_account_current
)
SELECT
    utilization_band,
    COUNT(*)                                                  AS accounts,
    ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER () * 100, 1) AS pct_of_accounts,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                               AS delinquency_rate_pct,
    -- Share of ALL 30+ delinquent accounts that fall in this band.
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / SUM(COUNT(*) FILTER (WHERE days_past_due >= 30)) OVER () * 100, 1)
                                                             AS pct_of_all_delinquents
FROM banded
GROUP BY utilization_band
ORDER BY utilization_band;


-- ----------------------------------------------------------------------------
-- 5.2  Delinquency rate by credit-file depth (thin vs. established file)
-- ----------------------------------------------------------------------------
SELECT
    CASE WHEN thin_file THEN 'Thin file (<24 mo)' ELSE 'Established file' END AS file_depth,
    COUNT(*)                                                  AS accounts,
    ROUND(AVG(credit_history_months), 0)                     AS avg_history_months,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                               AS delinquency_rate_pct,
    ROUND(COUNT(*) FILTER (WHERE charge_off_flag = 1)::numeric
          / COUNT(*) * 100, 2)                               AS charge_off_rate_pct
FROM v_account_current
GROUP BY thin_file
ORDER BY file_depth;


-- ----------------------------------------------------------------------------
-- 5.3  Interaction: utilization band x file depth.
--      The worst cell (high utilization AND thin file) is the root-cause segment.
-- ----------------------------------------------------------------------------
WITH banded AS (
    SELECT
        CASE WHEN utilization >= 0.60 THEN 'High util (>=60%)'
             ELSE 'Lower util (<60%)' END AS utilization_band,
        CASE WHEN thin_file THEN 'Thin file' ELSE 'Established' END AS file_depth,
        days_past_due
    FROM v_account_current
)
SELECT
    utilization_band,
    file_depth,
    COUNT(*)                                                  AS accounts,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                               AS delinquency_rate_pct
FROM banded
GROUP BY GROUPING SETS ((utilization_band, file_depth), ())
ORDER BY utilization_band NULLS LAST, file_depth NULLS LAST;


-- ----------------------------------------------------------------------------
-- 5.4  Concentration: how much of total delinquency sits in the combined
--      high-utilization OR thin-file population vs. the rest of the book.
-- ----------------------------------------------------------------------------
WITH flagged AS (
    SELECT
        (utilization >= 0.60 OR thin_file) AS high_risk_segment,
        days_past_due,
        ending_balance,
        charge_off_flag
    FROM v_account_current
)
SELECT
    CASE WHEN high_risk_segment THEN 'High-util or thin-file' ELSE 'Rest of book' END AS segment,
    COUNT(*)                                                  AS accounts,
    ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER () * 100, 1) AS pct_of_accounts,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / COUNT(*) * 100, 2)                               AS delinquency_rate_pct,
    ROUND(COUNT(*) FILTER (WHERE days_past_due >= 30)::numeric
          / SUM(COUNT(*) FILTER (WHERE days_past_due >= 30)) OVER () * 100, 1)
                                                             AS pct_of_all_delinquents,
    ROUND(SUM(ending_balance) FILTER (WHERE charge_off_flag = 1)
          / NULLIF(SUM(SUM(ending_balance) FILTER (WHERE charge_off_flag = 1)) OVER (), 0) * 100, 1)
                                                             AS pct_of_chargeoff_balance
FROM flagged
GROUP BY high_risk_segment
ORDER BY high_risk_segment DESC;
