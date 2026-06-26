"""
clean_validate.py
-----------------
Cleans and validates the raw credit-card source files produced by
`generate_raw_data.py` and writes analysis-ready tables to data/processed/.

Responsibilities
  1. Parse mixed date formats into ISO dates (open_date, statement_month).
  2. Strip currency formatting ("$1,234.50" -> 1234.50) on income, limits, and
     all statement money columns.
  3. Standardize employment status casing and fill blanks.
  4. Repair out-of-range values (impossible ages, negative payments, FICO
     outside 300-850).
  5. Remove exact duplicate rows (accounts and statements).
  6. Enforce referential integrity (every FK resolves to a parent row).
  7. Emit a data-quality report (Markdown + JSON) documenting every fix.

The processed files are what the PostgreSQL loader (sql/02_load.sql) ingests and
what the analytics step consumes, so this is the single source of truth for
"clean" data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW_DIR = os.path.join(ROOT, "data", "raw")
PROC_DIR = os.path.join(ROOT, "data", "processed")
REPORT_DIR = os.path.join(ROOT, "reports")

# Tracks every cleaning action for the data-quality report.
AUDIT: dict[str, dict] = {}


def log(table: str, key: str, value):
    AUDIT.setdefault(table, {})[key] = value


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def parse_date(value):
    """Parse the several date formats present in the raw files into a date."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return pd.NaT
    s = str(value).strip()
    if s == "" or s.lower() in {"nan", "nat", "none"}:
        return pd.NaT
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d %Y", "%B %d %Y"):
        try:
            return pd.Timestamp(datetime.strptime(s, fmt).date())
        except ValueError:
            continue
    return pd.to_datetime(s, errors="coerce")


def parse_money(value):
    """Strip currency symbols / thousands separators -> float (NaN if blank)."""
    if value is None:
        return np.nan
    s = str(value).strip()
    if s == "" or s.lower() in {"nan", "none", "n/a"}:
        return np.nan
    s = s.replace("$", "").replace(",", "").replace("USD", "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


# --------------------------------------------------------------------------- #
# Reference tables
# --------------------------------------------------------------------------- #
def clean_regions() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(RAW_DIR, "regions.csv")).drop_duplicates()
    log("regions", "rows_out", len(df))
    return df


def clean_card_products() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(RAW_DIR, "card_products.csv")).drop_duplicates()
    log("card_products", "rows_out", len(df))
    return df


def clean_cardholders() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(RAW_DIR, "cardholders.csv"), dtype=str)
    before = len(df)
    df = df.drop_duplicates()
    log("cardholders", "exact_duplicates_removed", before - len(df))

    df["cardholder_id"] = df["cardholder_id"].astype(int)
    df["region_id"] = df["region_id"].astype(int)

    # Income: strip currency formatting; impute missing with the median.
    df["annual_income"] = df["annual_income"].map(parse_money)
    missing_income = int(df["annual_income"].isna().sum())
    log("cardholders", "missing_income_imputed", missing_income)
    df["annual_income"] = df["annual_income"].fillna(round(df["annual_income"].median(), 2))

    # Age: anything outside a sane 18-100 band is a data-entry error -> NULL,
    # then impute with the median age (documented choice).
    df["age"] = pd.to_numeric(df["age"], errors="coerce")
    bad_age = (df["age"] < 18) | (df["age"] > 100)
    log("cardholders", "impossible_ages_fixed", int(bad_age.sum()))
    df.loc[bad_age, "age"] = np.nan
    df["age"] = df["age"].fillna(round(df["age"].median())).astype(int)

    # Employment status: title-case; blanks/missing -> "Unknown". Detect missing
    # with isna() (a NaN survives astype(str) as a missing value in modern pandas,
    # so a string == "nan" comparison would silently miss it).
    emp = df["employment_status"]
    stripped = emp.astype(str).str.strip()
    blanks = emp.isna() | stripped.isin(["", "nan", "None", "<NA>"])
    log("cardholders", "employment_blanks_filled", int(blanks.sum()))
    # str.title() normalizes casing and handles the hyphen ("Self-Employed").
    df["employment_status"] = stripped.where(~blanks, "Unknown").str.title()

    # FICO must sit in 300-850; clip out-of-range values to the boundary.
    df["fico_origination"] = pd.to_numeric(df["fico_origination"], errors="coerce")
    oor = (df["fico_origination"] < 300) | (df["fico_origination"] > 850)
    log("cardholders", "fico_out_of_range_clipped", int(oor.sum()))
    df["fico_origination"] = df["fico_origination"].clip(300, 850).astype(int)

    df["credit_history_months"] = pd.to_numeric(df["credit_history_months"], errors="coerce").fillna(0).astype(int)

    log("cardholders", "rows_out", len(df))
    return df.reset_index(drop=True)


def clean_accounts() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(RAW_DIR, "accounts.csv"), dtype=str)
    before = len(df)
    # Exact duplicates (the raw export double-listed some accounts).
    df = df.drop_duplicates()
    # Also dedupe on the key in case a duplicate differs only by formatting noise.
    df = df.drop_duplicates(subset=["account_id"])
    log("accounts", "duplicate_accounts_removed", before - len(df))

    df["account_id"] = df["account_id"].astype(int)
    df["cardholder_id"] = df["cardholder_id"].astype(int)
    df["product_id"] = df["product_id"].astype(int)
    df["open_date"] = df["open_date"].map(parse_date)
    df["credit_limit"] = df["credit_limit"].map(parse_money)

    # Credit limit must be positive.
    bad_limit = (df["credit_limit"].isna()) | (df["credit_limit"] <= 0)
    log("accounts", "nonpositive_limit_dropped", int(bad_limit.sum()))
    df = df[~bad_limit]
    df["credit_limit"] = df["credit_limit"].round(2)

    log("accounts", "rows_out", len(df))
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Transactional table
# --------------------------------------------------------------------------- #
MONEY_COLS = ["opening_balance", "purchase_amount", "payment_amount",
              "ending_balance", "minimum_due"]


def clean_statements() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(RAW_DIR, "monthly_statements.csv"), dtype=str)
    before = len(df)
    df = df.drop_duplicates()
    log("monthly_statements", "exact_duplicates_removed", before - len(df))

    df["statement_id"] = df["statement_id"].astype(int)
    df["account_id"] = df["account_id"].astype(int)
    df["statement_month"] = df["statement_month"].map(parse_date)

    for col in MONEY_COLS:
        df[col] = df[col].map(parse_money)

    # Negative payments are sign-entry errors -> take absolute value.
    neg_pay = df["payment_amount"] < 0
    log("monthly_statements", "negative_payments_corrected", int(neg_pay.sum()))
    df.loc[neg_pay, "payment_amount"] = df.loc[neg_pay, "payment_amount"].abs()

    # Missing payment means no payment was recorded that cycle -> 0.
    missing_pay = df["payment_amount"].isna()
    log("monthly_statements", "missing_payments_zeroed", int(missing_pay.sum()))
    df["payment_amount"] = df["payment_amount"].fillna(0.0)

    # Any remaining missing money fields -> 0 (a blank balance is a zero balance).
    for col in MONEY_COLS:
        df[col] = df[col].fillna(0.0).round(2)

    df["days_past_due"] = pd.to_numeric(df["days_past_due"], errors="coerce").fillna(0).astype(int)
    df["charge_off_flag"] = pd.to_numeric(df["charge_off_flag"], errors="coerce").fillna(0).astype(int)

    log("monthly_statements", "rows_out", len(df))
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Referential integrity
# --------------------------------------------------------------------------- #
def enforce_referential_integrity(tables: dict[str, pd.DataFrame]):
    region_ids = set(tables["regions"]["region_id"])
    product_ids = set(tables["card_products"]["product_id"])

    # cardholders -> regions
    ch = tables["cardholders"]
    bad = ~ch["region_id"].isin(region_ids)
    log("cardholders", "orphan_region_fk_dropped", int(bad.sum()))
    tables["cardholders"] = ch[~bad]
    cardholder_ids = set(tables["cardholders"]["cardholder_id"])

    # accounts -> cardholders, card_products
    ac = tables["accounts"]
    bad = ~ac["cardholder_id"].isin(cardholder_ids) | ~ac["product_id"].isin(product_ids)
    log("accounts", "orphan_fk_dropped", int(bad.sum()))
    tables["accounts"] = ac[~bad]
    account_ids = set(tables["accounts"]["account_id"])

    # monthly_statements -> accounts
    st = tables["monthly_statements"]
    bad = ~st["account_id"].isin(account_ids)
    log("monthly_statements", "orphan_account_fk_dropped", int(bad.sum()))
    tables["monthly_statements"] = st[~bad]

    return tables


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def write_report():
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "data_quality_report.json"), "w") as f:
        json.dump(AUDIT, f, indent=2)

    lines = [
        "# Data Quality Report",
        "",
        f"_Generated: {datetime.now():%Y-%m-%d %H:%M}_",
        "",
        "This report documents every cleaning and validation action applied when",
        "transforming the raw card-system exports into analysis-ready tables.",
        "",
    ]
    for table, actions in AUDIT.items():
        lines.append(f"## `{table}`")
        lines.append("")
        lines.append("| Check | Count |")
        lines.append("| --- | ---: |")
        for k, v in actions.items():
            lines.append(f"| {k.replace('_', ' ')} | {v} |")
        lines.append("")
    with open(os.path.join(REPORT_DIR, "data_quality_report.md"), "w") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    os.makedirs(PROC_DIR, exist_ok=True)

    tables = {
        "regions": clean_regions(),
        "card_products": clean_card_products(),
        "cardholders": clean_cardholders(),
        "accounts": clean_accounts(),
        "monthly_statements": clean_statements(),
    }

    tables = enforce_referential_integrity(tables)

    # Write processed tables with ISO dates.
    for name, df in tables.items():
        out = df.copy()
        for col in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out[col]):
                out[col] = out[col].dt.strftime("%Y-%m-%d")
        out.to_csv(os.path.join(PROC_DIR, f"{name}.csv"), index=False)

    write_report()

    print("Cleaned tables written to", PROC_DIR)
    for name, df in tables.items():
        print(f"  {name:<22} {len(df):>8} rows")
    print("\nData-quality report -> reports/data_quality_report.md")


if __name__ == "__main__":
    main()
