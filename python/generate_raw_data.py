"""
generate_raw_data.py
--------------------
Generates a realistic but synthetic consumer **credit-card portfolio** for the
Credit Card Portfolio Performance & Delinquency Analytics project.

The portfolio is ~50K accounts, each with a monthly statement panel (balance,
purchases, payments, minimum due, days-past-due) over a trailing window. The
delinquency dynamics are produced by a per-account monthly **roll-rate process**
(current -> 30 -> 60 -> 90 -> ... -> charge-off, with curing), driven by latent
account risk so that high-utilization and thin-file accounts genuinely default
more often. That makes the downstream root-cause analysis a real finding rather
than something baked in by hand.

The files are written the way real exports from a card-processing / collections
system usually look:

  * Income and balances stored as currency strings ("$54,200.00") on some rows
  * Mixed date formats (YYYY-MM-DD, MM/DD/YYYY, "Jan 05 2024")
  * Employment status with inconsistent casing and missing values
  * A handful of duplicate rows, out-of-range ages, and negative-payment
    sign errors

`clean_validate.py` turns these raw files into clean, validated tables.

Output: data/raw/*.csv
Reproducible: fixed random seed.
"""

from __future__ import annotations

import os
from datetime import date

import numpy as np
import pandas as pd

SEED = 42
rng = np.random.default_rng(SEED)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW_DIR = os.path.join(ROOT, "data", "raw")

# Portfolio size and observation window.
N_ACCOUNTS = 50_000
N_MONTHS = 12                       # trailing months of statements per account
WINDOW_END = date(2026, 5, 31)      # "today" in the project narrative is mid-2026

# Risk-model coefficients for the monthly miss-payment probability. Calibrated so
# the portfolio lands at a realistic ~6-8% point-in-time 30+ delinquency rate and
# a ~3-5% charge-off rate, with utilization and thin-file as the dominant drivers.
RISK_INTERCEPT = -5.28
RISK_BETA_FICO = -0.90    # higher FICO -> lower risk
RISK_BETA_INCOME = -0.30  # higher income -> lower risk
RISK_BETA_UTIL = 1.85     # high utilization -> higher risk   (primary driver)
RISK_BETA_THIN = 0.85     # thin credit file -> higher risk   (primary driver)
DELINQ_ACCEL = 0.13       # added miss prob per 30 days already past due (roll-forward)
MACRO_RAMP = 0.0035       # added miss prob per month of the window (rising-delinquency trend)
STATEMENT_MONTHS = list(
    pd.date_range(end=WINDOW_END, periods=N_MONTHS, freq="ME").date
)


# --------------------------------------------------------------------------- #
# Reference data: regions and card products
# --------------------------------------------------------------------------- #
def build_regions() -> pd.DataFrame:
    regions = ["Northeast", "Midwest", "South", "West"]
    return pd.DataFrame(
        {"region_id": range(1, len(regions) + 1), "region_name": regions}
    )


def build_card_products() -> pd.DataFrame:
    # name, base APR, annual fee, min FICO band the product is typically issued to
    products = [
        ("Secured",  0.2599,   0, 300),
        ("Standard", 0.2399,   0, 580),
        ("Gold",     0.2099,  95, 660),
        ("Platinum", 0.1799, 195, 720),
    ]
    rows = []
    for i, p in enumerate(products, start=1):
        rows.append(
            {
                "product_id": i,
                "product_name": p[0],
                "base_apr": p[1],
                "annual_fee": p[2],
                "_min_fico": p[3],
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Cardholders + accounts (one account per cardholder)
# --------------------------------------------------------------------------- #
EMPLOYMENT = ["Employed", "Self-Employed", "Retired", "Unemployed", "Student"]
EMPLOYMENT_P = [0.62, 0.12, 0.10, 0.06, 0.10]


def build_cardholders_accounts(products: pd.DataFrame):
    """Create cardholders (demographics) and their accounts (the credit product).

    Each account is given a latent monthly default propensity built from four
    drivers — low FICO, thin credit file, low income, and high starting
    utilization — so the portfolio analytics surface real, defensible segments.
    """
    n = N_ACCOUNTS
    ids = np.arange(1, n + 1)

    # --- Demographics -----------------------------------------------------
    age = rng.integers(21, 78, size=n)

    # Income is right-skewed and rises modestly with age.
    income = rng.lognormal(mean=10.9, sigma=0.45, size=n)
    income *= 1 + (age - 45) * 0.004
    income = np.clip(income, 14_000, 400_000).round(-2)

    employment = rng.choice(EMPLOYMENT, size=n, p=EMPLOYMENT_P)

    region_id = rng.integers(1, 5, size=n)

    # Length of credit history (months) at account open. A meaningful share are
    # "thin file": < 24 months of history. Correlated with age but noisy.
    credit_history_months = np.clip(
        (age - 18) * 12 * rng.uniform(0.15, 0.8, size=n), 1, 480
    ).astype(int)

    # FICO at origination: anchored by income and history, with real spread.
    fico = (
        540
        + 80 * (np.log(income) - np.log(45_000))
        + 0.04 * credit_history_months
        + rng.normal(0, 55, size=n)
    )
    fico = np.clip(fico, 300, 850).round().astype(int)

    # --- Account terms ----------------------------------------------------
    # Product tier issued mostly by FICO band.
    prod_min = products.set_index("product_id")["_min_fico"].to_dict()
    product_id = np.ones(n, dtype=int)
    for pid in sorted(prod_min, reverse=True):       # assign highest tier first
        eligible = fico >= prod_min[pid]
        # only overwrite where still at the default tier 1 and eligible
        take = eligible & (product_id == 1) & (rng.random(n) < 0.85)
        product_id[take] = pid
    # Secured (tier 1) keeps everyone not promoted.

    # Credit limit scales with FICO and income; secured cards are small.
    limit = (
        income * rng.uniform(0.12, 0.45, size=n)
        * (1 + (fico - 600) / 250)
    )
    limit = np.where(product_id == 1, np.minimum(limit, 1_500), limit)
    credit_limit = np.clip(limit, 300, 50_000)
    credit_limit = (np.round(credit_limit / 100) * 100).astype(int)

    # Account open date: opened 6 months to ~8 years before the window end.
    days_back = rng.integers(180, 8 * 365, size=n)
    open_date = np.array(
        [WINDOW_END - pd.Timedelta(days=int(d)) for d in days_back]
    )

    # --- Latent monthly default propensity -------------------------------
    thin_file = (credit_history_months < 24).astype(float)
    # Starting utilization (carried balance / limit) — revolvers run hot.
    start_util = np.clip(rng.beta(2.0, 3.2, size=n) + thin_file * 0.08, 0.0, 1.05)

    fico_z = (fico - 680) / 70.0
    income_z = (np.log(income) - np.log(55_000)) / 0.6

    # Linear risk index -> monthly probability of missing a payment.
    risk_index = (
        RISK_INTERCEPT
        + RISK_BETA_FICO * fico_z
        + RISK_BETA_INCOME * income_z
        + RISK_BETA_UTIL * start_util    # high utilization -> higher risk (key driver)
        + RISK_BETA_THIN * thin_file     # thin file -> higher risk        (key driver)
        + rng.normal(0, 0.35, size=n)
    )
    miss_prob = 1 / (1 + np.exp(-risk_index))   # baseline monthly miss probability

    cardholders = pd.DataFrame(
        {
            "cardholder_id": ids,
            "age": age,
            "annual_income": income.astype(int),
            "employment_status": employment,
            "region_id": region_id,
            "fico_origination": fico,
            "credit_history_months": credit_history_months,
        }
    )
    accounts = pd.DataFrame(
        {
            "account_id": ids,
            "cardholder_id": ids,
            "product_id": product_id,
            "open_date": open_date,
            "credit_limit": credit_limit,
            # latent fields (underscored) drive the simulation, not exported raw
            "_start_util": start_util,
            "_miss_prob": miss_prob,
            "_thin_file": thin_file,
        }
    )
    return cardholders, accounts


# --------------------------------------------------------------------------- #
# Monthly statement panel (vectorized roll-rate simulation)
# --------------------------------------------------------------------------- #
def build_statements(accounts: pd.DataFrame) -> pd.DataFrame:
    """Simulate the monthly billing cycle for every account, vectorized across
    accounts with one step per statement month.

    Roll-rate logic: each month an account either makes at least its minimum
    payment (stays / becomes current) or misses (days-past-due rolls forward by
    30). At 180 DPD the account is charged off and frozen. The monthly miss
    probability rises with utilization, so balances feed back into delinquency.
    """
    n = len(accounts)
    limit = accounts["credit_limit"].to_numpy(dtype=float)
    miss_prob = accounts["_miss_prob"].to_numpy()
    start_util = accounts["_start_util"].to_numpy()
    acct_ids = accounts["account_id"].to_numpy()

    # State carried month to month.
    balance = np.clip(start_util * limit, 0, limit * 1.05).round(2)
    dpd = np.zeros(n, dtype=int)
    charged_off = np.zeros(n, dtype=bool)

    # Spend propensity: share of available room used in purchases each month.
    spend_rate = rng.uniform(0.06, 0.38, size=n)

    rows = []
    stmt_id = 1
    for m_idx, month in enumerate(STATEMENT_MONTHS):
        active = ~charged_off

        opening = balance.copy()

        # Purchases: a share of remaining open-to-buy, zero once charged off.
        room = np.clip(limit - balance, 0, None)
        purchases = np.where(active, room * spend_rate * rng.uniform(0.5, 1.5, n), 0.0)
        purchases = np.round(purchases, 2)

        pre_payment_balance = np.round(opening + purchases, 2)

        # Minimum due: 2% of balance, floored at $25 (or the full balance if low).
        minimum_due = np.maximum(np.round(pre_payment_balance * 0.02, 2), 25.0)
        minimum_due = np.minimum(minimum_due, pre_payment_balance)

        # Does the account make at least its minimum payment this month?
        # Delinquent accounts are progressively less likely to pay, and a gentle
        # macro stress ramp lifts miss rates over the window (rising delinquency).
        eff_miss = np.clip(
            miss_prob + dpd / 30 * DELINQ_ACCEL + m_idx * MACRO_RAMP, 0, 0.97
        )
        misses = (rng.random(n) < eff_miss) & active & (pre_payment_balance > 0)
        pays = active & ~misses & (pre_payment_balance > 0)

        # Payment amount: payers cover anywhere from the minimum up to the full
        # balance. Full-payers (transactors) clear it; revolvers pay a partial
        # share and carry the rest, so the book keeps a realistic balance.
        pay_frac = rng.uniform(0.05, 0.6, size=n)               # revolver pay-down share
        full_payer = rng.random(n) < (0.30 * (1 - miss_prob))   # transactors pay in full
        payment = np.where(
            pays,
            np.where(full_payer, pre_payment_balance,
                     np.maximum(minimum_due, pre_payment_balance * pay_frac)),
            0.0,
        )
        payment = np.round(np.minimum(payment, pre_payment_balance), 2)

        # Interest accrues on the carried balance for revolvers (simple monthly).
        carried = np.clip(pre_payment_balance - payment, 0, None)
        interest = np.where(active, np.round(carried * 0.02, 2), 0.0)
        ending = np.round(carried + interest, 2)

        # Roll DPD: paid >= minimum -> current; otherwise +30.
        made_min = payment >= (minimum_due - 0.005)
        new_dpd = dpd.copy()
        new_dpd[active & made_min] = 0
        new_dpd[active & ~made_min] = dpd[active & ~made_min] + 30

        # Charge-off at 180 DPD: freeze the account at its written-off balance.
        newly_co = active & (new_dpd >= 180)
        charged_off = charged_off | newly_co

        rows.append(
            pd.DataFrame(
                {
                    "statement_id": np.arange(stmt_id, stmt_id + n),
                    "account_id": acct_ids,
                    "statement_month": month,
                    "opening_balance": opening,
                    "purchase_amount": purchases,
                    "payment_amount": payment,
                    "ending_balance": ending,
                    "minimum_due": minimum_due,
                    "days_past_due": new_dpd,
                    "charge_off_flag": charged_off.astype(int),
                }
            )
        )
        stmt_id += n

        # Advance state. Charged-off accounts freeze; others carry the new balance.
        dpd = new_dpd
        balance = np.where(charged_off, ending, ending)

    statements = pd.concat(rows, ignore_index=True)
    # Drop the statements that occur after an account has already charged off in
    # a *previous* month (the charge-off month itself is kept as the event row).
    statements = _trim_post_chargeoff(statements)
    return statements


def _trim_post_chargeoff(stmt: pd.DataFrame) -> pd.DataFrame:
    """Keep statements up to and including the charge-off month, then stop —
    a charged-off account no longer produces active billing statements."""
    stmt = stmt.sort_values(["account_id", "statement_month"]).reset_index(drop=True)
    # Within each account, count charged-off rows seen so far (inclusive). The
    # charge-off month is the first row with cumulative count == 1; any row with
    # a prior charged-off month has a cumulative count > 1 and is dropped.
    co_cum = stmt.groupby("account_id")["charge_off_flag"].cumsum()
    keep = co_cum <= 1
    return stmt[keep].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Messiness injectors (make the cleaning step real)
# --------------------------------------------------------------------------- #
def _format_date_messy(d) -> str:
    d = pd.Timestamp(d).date()
    r = rng.random()
    if r < 0.5:
        return d.strftime("%Y-%m-%d")
    if r < 0.8:
        return d.strftime("%m/%d/%Y")
    return d.strftime("%b %d %Y")


def _money_messy(v, missing_p=0.0) -> str:
    if rng.random() < missing_p:
        return ""
    r = rng.random()
    if r < 0.4:
        return f"${v:,.2f}"
    if r < 0.6:
        return f"{v:.2f}"
    return f"{v}"


def messify_cardholders(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # Income as messy currency strings, a few missing.
    out["annual_income"] = out["annual_income"].map(lambda v: _money_messy(v, missing_p=0.03))
    # Inconsistent employment casing; a few blanks.
    def vary_emp(s):
        r = rng.random()
        if r < 0.05:
            return ""
        if r < 0.2:
            return s.upper()
        if r < 0.3:
            return s.lower()
        return s
    out["employment_status"] = out["employment_status"].map(vary_emp)
    # A few impossible ages (data-entry errors).
    bad_age_idx = rng.choice(out.index, size=40, replace=False)
    out.loc[bad_age_idx, "age"] = rng.choice([0, 1, 199, 250], size=len(bad_age_idx))
    return out


def messify_accounts(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["open_date"] = out["open_date"].map(_format_date_messy)
    out["credit_limit"] = out["credit_limit"].map(lambda v: _money_messy(v))
    # Inject a few exact duplicate account rows.
    dups = out.sample(n=25, random_state=SEED)
    out = pd.concat([out, dups], ignore_index=True)
    return out


def messify_statements(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["statement_month"] = out["statement_month"].map(_format_date_messy)

    # Currency formatting on a fraction of the monetary columns (keeps the big
    # file from ballooning, but still forces real parsing in the clean step).
    mask = rng.random(len(out)) < 0.18
    for col in ["opening_balance", "purchase_amount", "payment_amount",
                "ending_balance", "minimum_due"]:
        vals = out[col].to_numpy()
        formatted = np.array([f"{v}" for v in vals], dtype=object)
        formatted[mask] = [f"${v:,.2f}" for v in vals[mask]]
        out[col] = formatted

    # A handful of negative-payment sign errors (data-entry).
    neg_idx = rng.choice(out.index, size=200, replace=False)
    out.loc[neg_idx, "payment_amount"] = out.loc[neg_idx, "payment_amount"].map(
        lambda s: f"-{s}" if not str(s).startswith("-") else s
    )

    # Some missing payment amounts.
    miss_idx = rng.choice(out.index, size=300, replace=False)
    out.loc[miss_idx, "payment_amount"] = ""

    # A few exact duplicate statement rows.
    dups = out.sample(n=50, random_state=SEED)
    out = pd.concat([out, dups], ignore_index=True)
    return out


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    os.makedirs(RAW_DIR, exist_ok=True)

    regions = build_regions()
    products = build_card_products()
    cardholders, accounts = build_cardholders_accounts(products)
    statements = build_statements(accounts)

    # Public-facing columns only (drop latent "_" simulation drivers).
    accounts_public = accounts[[c for c in accounts.columns if not c.startswith("_")]]
    products_public = products[[c for c in products.columns if not c.startswith("_")]]

    # Apply messiness once (injectors advance the RNG; call each a single time).
    cardholders_raw = messify_cardholders(cardholders)
    accounts_raw = messify_accounts(accounts_public)
    statements_raw = messify_statements(statements)

    regions.to_csv(os.path.join(RAW_DIR, "regions.csv"), index=False)
    products_public.to_csv(os.path.join(RAW_DIR, "card_products.csv"), index=False)
    cardholders_raw.to_csv(os.path.join(RAW_DIR, "cardholders.csv"), index=False)
    accounts_raw.to_csv(os.path.join(RAW_DIR, "accounts.csv"), index=False)
    statements_raw.to_csv(os.path.join(RAW_DIR, "monthly_statements.csv"), index=False)

    print("Raw source files written to", RAW_DIR)
    print(f"  regions:            {len(regions):>8}")
    print(f"  card_products:      {len(products_public):>8}")
    print(f"  cardholders:        {len(cardholders_raw):>8}")
    print(f"  accounts (+dups):   {len(accounts_raw):>8}")
    print(f"  monthly_statements: {len(statements_raw):>8}")


if __name__ == "__main__":
    main()
