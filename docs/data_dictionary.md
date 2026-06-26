# Data Dictionary

Column-level documentation for the five tables in the credit-card portfolio data
model. Types reflect the PostgreSQL schema in [`sql/01_schema.sql`](../sql/01_schema.sql).
The data is synthetic (see [the note on the data](#note-on-the-data)).

## `regions`
Reference table of geographic regions.

| Column | Type | Description |
| --- | --- | --- |
| `region_id` | INTEGER (PK) | Surrogate key. |
| `region_name` | TEXT | Region name (Northeast, Midwest, South, West). |

## `card_products`
Reference table of card products / tiers.

| Column | Type | Description |
| --- | --- | --- |
| `product_id` | INTEGER (PK) | Surrogate key. |
| `product_name` | TEXT | Product tier: Secured, Standard, Gold, Platinum. |
| `base_apr` | NUMERIC(6,4) | Base annual percentage rate (e.g. 0.2399 = 23.99%). |
| `annual_fee` | NUMERIC(8,2) | Annual fee in dollars. |

## `cardholders`
Customer dimension — one row per cardholder, demographics at account level.

| Column | Type | Description |
| --- | --- | --- |
| `cardholder_id` | INTEGER (PK) | Surrogate key. |
| `age` | INTEGER | Cardholder age (validated to 18–100). |
| `annual_income` | NUMERIC(12,2) | Self-reported annual income (USD). |
| `employment_status` | TEXT | Employed, Self-Employed, Retired, Unemployed, Student, or Unknown. |
| `region_id` | INTEGER (FK → regions) | Region of residence. |
| `fico_origination` | INTEGER | FICO credit score at account opening (300–850). |
| `credit_history_months` | INTEGER | Length of credit history at opening, in months. Drives the **thin-file** flag (< 24 months). |

## `accounts`
Account dimension — one row per account (one account per cardholder).

| Column | Type | Description |
| --- | --- | --- |
| `account_id` | INTEGER (PK) | Surrogate key. |
| `cardholder_id` | INTEGER (FK → cardholders) | Owner of the account. |
| `product_id` | INTEGER (FK → card_products) | Card product / tier. |
| `open_date` | DATE | Account opening date. |
| `credit_limit` | NUMERIC(12,2) | Assigned credit limit (USD). |

## `monthly_statements`
Transactional fact table — one row per account per statement cycle.

| Column | Type | Description |
| --- | --- | --- |
| `statement_id` | INTEGER (PK) | Surrogate key. |
| `account_id` | INTEGER (FK → accounts) | Account the statement belongs to. |
| `statement_month` | DATE | Statement cycle (month-end). |
| `opening_balance` | NUMERIC(12,2) | Balance carried into the cycle. |
| `purchase_amount` | NUMERIC(12,2) | Purchases posted during the cycle. |
| `payment_amount` | NUMERIC(12,2) | Payments received during the cycle. |
| `ending_balance` | NUMERIC(12,2) | Balance at cycle close (includes accrued interest on revolved balance). |
| `minimum_due` | NUMERIC(12,2) | Minimum payment due (2% of balance, floored at $25). |
| `days_past_due` | INTEGER | Delinquency aging: 0 (current), 30, 60, 90, 120, 150, 180. |
| `charge_off_flag` | INTEGER | 1 once the account reaches 180 DPD and is charged off, else 0. |

## Derived measures (computed in analysis, not stored)

| Measure | Definition |
| --- | --- |
| **Credit utilization** | `ending_balance / credit_limit` (account), or `SUM(ending_balance) / SUM(credit_limit)` (portfolio). |
| **30/60/90+ delinquency rate** | Share of accounts with `days_past_due >= 30 / 60 / 90` at the latest cycle. |
| **Charge-off rate** | Share of accounts with `charge_off_flag = 1` (or charged-off balance / total balance). |
| **Thin file** | `credit_history_months < 24`. |
| **Risk tier** | Rule-based points on FICO band + utilization band + thin-file (see [`sql/04_risk_segmentation.sql`](../sql/04_risk_segmentation.sql)). |
| **Roll-forward / cure rate** | Probability an account moves to a worse / current bucket next cycle (see [`sql/06_trend_analysis.sql`](../sql/06_trend_analysis.sql)). |

## Note on the data

The data is **synthetic**, generated with a fixed random seed
([`python/generate_raw_data.py`](../python/generate_raw_data.py)) so the whole
pipeline is reproducible. Each account carries a latent monthly default
propensity built from low FICO, high utilization, thin credit file, and low
income, and delinquency evolves through a monthly roll-rate process. The result
is an internally consistent portfolio in which the KPIs, segments, and drivers
reflect real patterns rather than noise. The schema and analysis would apply
unchanged to a real card-processing extract.
