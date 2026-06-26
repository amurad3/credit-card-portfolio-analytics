-- ============================================================================
-- 01_schema.sql
-- Relational schema for the Credit Card Portfolio Performance & Delinquency
-- Analytics project.
--
-- Design notes
--   * Five related tables: reference data (regions, card_products), the customer
--     and account dimensions (cardholders, accounts), and the transactional
--     monthly statement panel (monthly_statements).
--   * One cardholder owns one account; the two are split so customer
--     demographics and account terms can be modeled and queried independently.
--   * Foreign keys enforce the same referential integrity that the Python
--     cleaning step validates, so the database stays consistent even if loaded
--     on its own.
--   * Derived measures (utilization, delinquency buckets) are computed in the
--     analysis queries rather than stored, keeping the base tables clean.
--
-- Target: PostgreSQL 13+
-- Run:    psql -d creditcard -f sql/01_schema.sql
-- ============================================================================

DROP TABLE IF EXISTS monthly_statements CASCADE;
DROP TABLE IF EXISTS accounts           CASCADE;
DROP TABLE IF EXISTS cardholders        CASCADE;
DROP TABLE IF EXISTS card_products      CASCADE;
DROP TABLE IF EXISTS regions            CASCADE;

-- ---------------------------------------------------------------------------
-- Reference tables
-- ---------------------------------------------------------------------------
CREATE TABLE regions (
    region_id    INTEGER PRIMARY KEY,
    region_name  TEXT    NOT NULL
);

CREATE TABLE card_products (
    product_id    INTEGER PRIMARY KEY,
    product_name  TEXT    NOT NULL,
    base_apr      NUMERIC(6, 4) NOT NULL CHECK (base_apr >= 0),
    annual_fee    NUMERIC(8, 2) NOT NULL CHECK (annual_fee >= 0)
);

-- ---------------------------------------------------------------------------
-- Customer + account dimensions
-- ---------------------------------------------------------------------------
CREATE TABLE cardholders (
    cardholder_id         INTEGER PRIMARY KEY,
    age                   INTEGER CHECK (age BETWEEN 18 AND 100),
    annual_income         NUMERIC(12, 2) CHECK (annual_income >= 0),
    employment_status     TEXT,
    region_id             INTEGER NOT NULL REFERENCES regions (region_id),
    fico_origination      INTEGER CHECK (fico_origination BETWEEN 300 AND 850),
    credit_history_months INTEGER CHECK (credit_history_months >= 0)
);

CREATE TABLE accounts (
    account_id      INTEGER PRIMARY KEY,
    cardholder_id   INTEGER NOT NULL REFERENCES cardholders (cardholder_id),
    product_id      INTEGER NOT NULL REFERENCES card_products (product_id),
    open_date       DATE    NOT NULL,
    credit_limit    NUMERIC(12, 2) NOT NULL CHECK (credit_limit > 0)
);

-- ---------------------------------------------------------------------------
-- Transactional table: one row per account per statement cycle
-- ---------------------------------------------------------------------------
CREATE TABLE monthly_statements (
    statement_id     INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES accounts (account_id),
    statement_month  DATE    NOT NULL,
    opening_balance  NUMERIC(12, 2) NOT NULL,
    purchase_amount  NUMERIC(12, 2) NOT NULL,
    payment_amount   NUMERIC(12, 2) NOT NULL,
    ending_balance   NUMERIC(12, 2) NOT NULL,
    minimum_due      NUMERIC(12, 2) NOT NULL,
    days_past_due    INTEGER NOT NULL CHECK (days_past_due >= 0),
    charge_off_flag  INTEGER NOT NULL CHECK (charge_off_flag IN (0, 1))
);

-- ---------------------------------------------------------------------------
-- Indexes to support the analytical joins / filters
-- ---------------------------------------------------------------------------
CREATE INDEX idx_accounts_cardholder ON accounts (cardholder_id);
CREATE INDEX idx_accounts_product    ON accounts (product_id);
CREATE INDEX idx_ch_region           ON cardholders (region_id);
CREATE INDEX idx_stmt_account        ON monthly_statements (account_id);
CREATE INDEX idx_stmt_month          ON monthly_statements (statement_month);
CREATE INDEX idx_stmt_account_month  ON monthly_statements (account_id, statement_month);
