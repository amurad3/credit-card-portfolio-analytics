-- ============================================================================
-- 02_load.sql
-- Loads the cleaned, analysis-ready CSVs (data/processed/) into the schema.
--
-- Uses psql's \copy so the paths are client-side and relative to the repo root.
-- Run from the repository root so the relative paths resolve:
--     psql -d creditcard -f sql/02_load.sql
--
-- Load order respects foreign keys: reference tables first, then the customer
-- and account dimensions, then the statement panel.
-- ============================================================================

\copy regions            FROM 'data/processed/regions.csv'            WITH (FORMAT csv, HEADER true);
\copy card_products      FROM 'data/processed/card_products.csv'      WITH (FORMAT csv, HEADER true);
\copy cardholders        FROM 'data/processed/cardholders.csv'        WITH (FORMAT csv, HEADER true);
\copy accounts           FROM 'data/processed/accounts.csv'           WITH (FORMAT csv, HEADER true);
\copy monthly_statements FROM 'data/processed/monthly_statements.csv' WITH (FORMAT csv, HEADER true);

-- Quick row-count sanity check after loading.
SELECT 'regions'            AS table_name, COUNT(*) AS rows FROM regions
UNION ALL SELECT 'card_products',      COUNT(*) FROM card_products
UNION ALL SELECT 'cardholders',        COUNT(*) FROM cardholders
UNION ALL SELECT 'accounts',           COUNT(*) FROM accounts
UNION ALL SELECT 'monthly_statements', COUNT(*) FROM monthly_statements
ORDER BY table_name;
