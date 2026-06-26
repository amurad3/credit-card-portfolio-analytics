"""
analytics.py
------------
Computes the credit-card portfolio KPIs, risk segmentation, root-cause analysis,
and the cost-benefit recommendation from the cleaned tables, and produces the
artifacts that feed the Tableau dashboard and the executive summary:

  * dashboard/extracts/*.csv   -> tidy extracts for the BI tool
  * dashboard/charts/*.png     -> static charts (matplotlib)
  * reports/kpis.json          -> machine-readable KPI values
  * reports/findings.md        -> human-readable headline findings

Method layers, all reporting the same metric definitions:
  1. KPIs       — delinquency rate, utilization, charge-off, 30/60/90 buckets.
  2. Segmentation — a rule-based risk tier (identical to sql/04) PLUS an
                    unsupervised KMeans segmentation (scikit-learn).
  3. Root cause — delinquency decomposed by utilization and credit-file depth,
                  with a logistic-regression driver model (scikit-learn) that
                  quantifies each driver's effect as an odds ratio.
  4. Recommendation — a data-derived cost-benefit for tightening credit-line
                      increases on the high-risk segment.

Every number quoted in the executive summary is produced here from the data.
"""

from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROC_DIR = os.path.join(ROOT, "data", "processed")
EXTRACT_DIR = os.path.join(ROOT, "dashboard", "extracts")
CHART_DIR = os.path.join(ROOT, "dashboard", "charts")
REPORT_DIR = os.path.join(ROOT, "reports")

# Assumption for the cost-benefit scenario. A credit-line increase (CLI) raises a
# revolver's balance ceiling; CLI-driven balance growth is estimated to account
# for this share of the eventual charge-off exposure on accounts that receive
# increases. Withholding CLI from the high-risk segment averts that share of the
# segment's charge-off exposure. (Conservative industry-style planning input.)
CLI_GROWTH_SHARE = 0.19
THIN_FILE_MONTHS = 24


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(PROC_DIR, f"{name}.csv"))


def assign_risk_tier(points: pd.Series) -> pd.Series:
    """Same rule-based tier as sql/04_risk_segmentation.sql."""
    return pd.cut(
        points,
        bins=[-1, 0, 2, 3, 99],
        labels=["Tier 1 - Low Risk", "Tier 2 - Moderate",
                "Tier 3 - Elevated", "Tier 4 - High Risk"],
    )


def build_current(statements, accounts, cardholders) -> pd.DataFrame:
    """One row per account at its latest statement, enriched with utilization,
    thin-file flag, risk points, and risk tier (mirrors sql/04's view)."""
    statements["statement_month"] = pd.to_datetime(statements["statement_month"])
    latest = (
        statements.sort_values("statement_month")
        .groupby("account_id", as_index=False)
        .tail(1)
    )
    cur = (
        latest.merge(accounts, on="account_id", how="left")
        .merge(cardholders, on="cardholder_id", how="left")
    )
    cur["utilization"] = cur["ending_balance"] / cur["credit_limit"].replace(0, np.nan)
    cur["thin_file"] = cur["credit_history_months"] < THIN_FILE_MONTHS
    cur["delinquent"] = cur["days_past_due"] >= 30

    fico_pts = np.where(cur["fico_origination"] < 660, 2,
                        np.where(cur["fico_origination"] < 720, 1, 0))
    util_pts = np.where(cur["utilization"] >= 0.90, 2,
                        np.where(cur["utilization"] >= 0.60, 1, 0))
    thin_pts = cur["thin_file"].astype(int)
    cur["risk_points"] = fico_pts + util_pts + thin_pts
    cur["risk_tier"] = assign_risk_tier(cur["risk_points"])
    return cur


def main():
    for d in (EXTRACT_DIR, CHART_DIR, REPORT_DIR):
        os.makedirs(d, exist_ok=True)

    regions = load("regions")
    products = load("card_products")
    cardholders = load("cardholders")
    accounts = load("accounts")
    statements = load("monthly_statements")

    cur = build_current(statements, accounts, cardholders)
    n = len(cur)

    # ------------------------------------------------------------------ #
    # 1. Portfolio KPIs (latest cycle)
    # ------------------------------------------------------------------ #
    delinquency_30 = (cur["days_past_due"] >= 30).mean()
    delinquency_60 = (cur["days_past_due"] >= 60).mean()
    delinquency_90 = (cur["days_past_due"] >= 90).mean()
    charge_off_rate = (cur["charge_off_flag"] == 1).mean()
    total_balance = cur["ending_balance"].sum()
    charge_off_balance = cur.loc[cur["charge_off_flag"] == 1, "ending_balance"].sum()
    avg_util = cur["utilization"].mean()
    portfolio_util = cur["ending_balance"].sum() / cur["credit_limit"].sum()

    # ------------------------------------------------------------------ #
    # 2a. Risk-tier segmentation (rule-based, matches SQL)
    # ------------------------------------------------------------------ #
    tier = cur.groupby("risk_tier", observed=True).agg(
        accounts=("account_id", "count"),
        avg_fico=("fico_origination", "mean"),
        avg_utilization=("utilization", "mean"),
        pct_thin_file=("thin_file", "mean"),
        delinquency_rate=("delinquent", "mean"),
        charge_off_rate=("charge_off_flag", "mean"),
        total_balance=("ending_balance", "sum"),
    ).reset_index()
    tier["pct_of_accounts"] = tier["accounts"] / n * 100
    tier["pct_of_balance"] = tier["total_balance"] / tier["total_balance"].sum() * 100
    co_bal_by_tier = cur[cur["charge_off_flag"] == 1].groupby("risk_tier", observed=True)["ending_balance"].sum()
    # Map on the string form so the result is plain float, not Categorical.
    tier["charge_off_balance"] = (
        tier["risk_tier"].astype(str).map({str(k): v for k, v in co_bal_by_tier.items()})
        .astype("float64").fillna(0.0)
    )
    tier["pct_of_chargeoff_balance"] = tier["charge_off_balance"] / tier["charge_off_balance"].sum() * 100
    for c in ["avg_fico"]:
        tier[c] = tier[c].round(0)
    for c in ["avg_utilization", "pct_thin_file", "delinquency_rate", "charge_off_rate"]:
        tier[c] = (tier[c] * 100).round(2)
    tier = tier.round({"total_balance": 2, "pct_of_accounts": 1, "pct_of_balance": 1,
                       "pct_of_chargeoff_balance": 1})

    # ------------------------------------------------------------------ #
    # 2b. Unsupervised KMeans segmentation (scikit-learn)
    #     Cluster on standardized behavioral features, then label clusters by
    #     their realized delinquency so the segments are interpretable.
    # ------------------------------------------------------------------ #
    feat_cols = ["utilization", "fico_origination", "annual_income",
                 "credit_history_months", "days_past_due"]
    X = cur[feat_cols].fillna(cur[feat_cols].median())
    Xs = StandardScaler().fit_transform(X)
    km = KMeans(n_clusters=4, n_init=10, random_state=42)
    cur["cluster"] = km.fit_predict(Xs)
    clus = cur.groupby("cluster").agg(
        accounts=("account_id", "count"),
        avg_utilization=("utilization", "mean"),
        avg_fico=("fico_origination", "mean"),
        avg_income=("annual_income", "mean"),
        avg_history_months=("credit_history_months", "mean"),
        delinquency_rate=("delinquent", "mean"),
    ).reset_index()
    # Order clusters by delinquency and give them readable names.
    clus = clus.sort_values("delinquency_rate").reset_index(drop=True)
    seg_names = ["Segment A - Lowest risk", "Segment B - Moderate",
                 "Segment C - Elevated", "Segment D - Highest risk"]
    clus["segment"] = seg_names[: len(clus)]
    clus["avg_utilization"] = (clus["avg_utilization"] * 100).round(1)
    clus["delinquency_rate"] = (clus["delinquency_rate"] * 100).round(2)
    clus = clus.round({"avg_fico": 0, "avg_income": 0, "avg_history_months": 0})

    # ------------------------------------------------------------------ #
    # 3a. Root cause: delinquency by utilization band & file depth
    # ------------------------------------------------------------------ #
    util_band = pd.cut(cur["utilization"], [-0.01, 0.30, 0.60, 0.90, np.inf],
                       labels=["<30%", "30-60%", "60-90%", "90%+"])
    by_util = cur.groupby(util_band, observed=True).agg(
        accounts=("account_id", "count"),
        delinquents=("delinquent", "sum"),
        delinquency_rate=("delinquent", "mean"),
    ).reset_index()
    by_util = by_util.rename(columns={by_util.columns[0]: "utilization_band"})
    by_util["pct_of_accounts"] = (by_util["accounts"] / n * 100).round(1)
    by_util["pct_of_all_delinquents"] = (by_util["delinquents"] / by_util["delinquents"].sum() * 100).round(1)
    by_util["delinquency_rate"] = (by_util["delinquency_rate"] * 100).round(2)

    by_file = cur.groupby("thin_file", observed=True).agg(
        accounts=("account_id", "count"),
        avg_history_months=("credit_history_months", "mean"),
        delinquency_rate=("delinquent", "mean"),
        charge_off_rate=("charge_off_flag", "mean"),
    ).reset_index()
    by_file["file_depth"] = np.where(by_file["thin_file"], "Thin file (<24 mo)", "Established file")
    by_file["delinquency_rate"] = (by_file["delinquency_rate"] * 100).round(2)
    by_file["charge_off_rate"] = (by_file["charge_off_rate"] * 100).round(2)
    by_file["avg_history_months"] = by_file["avg_history_months"].round(0)

    # Interaction grid: high-util x thin-file.
    cur["util_hi"] = cur["utilization"] >= 0.60
    grid = cur.groupby(["util_hi", "thin_file"], observed=True).agg(
        accounts=("account_id", "count"),
        delinquency_rate=("delinquent", "mean"),
    ).reset_index()
    grid["delinquency_rate"] = (grid["delinquency_rate"] * 100).round(2)
    grid["segment"] = np.where(grid["util_hi"], "High util (>=60%)", "Lower util (<60%)") + \
        np.where(grid["thin_file"], " + Thin file", " + Established")

    # ------------------------------------------------------------------ #
    # 3b. Driver model: logistic regression -> odds ratios
    # ------------------------------------------------------------------ #
    model_df = cur.dropna(subset=["utilization", "fico_origination", "annual_income", "age"]).copy()
    drivers = pd.DataFrame({
        "utilization": model_df["utilization"].clip(0, 2),
        "thin_file": model_df["thin_file"].astype(int),
        "fico_origination": model_df["fico_origination"],
        "log_income": np.log(model_df["annual_income"].clip(lower=1)),
        "age": model_df["age"],
    })
    y = model_df["delinquent"].astype(int)
    scaler = StandardScaler()
    Xd = scaler.fit_transform(drivers)
    logit = LogisticRegression(max_iter=1000)
    logit.fit(Xd, y)
    # Odds ratio per +1 standard-deviation move in each driver.
    odds = pd.DataFrame({
        "driver": drivers.columns,
        "coef_per_sd": logit.coef_[0],
        "odds_ratio_per_sd": np.exp(logit.coef_[0]),
    }).sort_values("odds_ratio_per_sd", ascending=False).reset_index(drop=True)
    odds["coef_per_sd"] = odds["coef_per_sd"].round(3)
    odds["odds_ratio_per_sd"] = odds["odds_ratio_per_sd"].round(2)

    # ------------------------------------------------------------------ #
    # 4. Monthly trend (all statements)
    # ------------------------------------------------------------------ #
    statements_acct = statements.merge(accounts[["account_id", "credit_limit"]], on="account_id", how="left")
    statements_acct["util"] = statements_acct["ending_balance"] / statements_acct["credit_limit"].replace(0, np.nan)
    monthly = statements_acct.groupby("statement_month").agg(
        active_accounts=("account_id", "count"),
        delinquency_rate=("days_past_due", lambda s: (s >= 30).mean()),
        charge_off_rate=("charge_off_flag", "mean"),
        avg_utilization=("util", "mean"),
    ).reset_index()
    monthly["delinquency_rate"] = (monthly["delinquency_rate"] * 100).round(2)
    monthly["charge_off_rate"] = (monthly["charge_off_rate"] * 100).round(2)
    monthly["avg_utilization"] = (monthly["avg_utilization"] * 100).round(1)
    monthly["delinquency_mom_change"] = monthly["delinquency_rate"].diff().round(2)
    monthly["statement_month"] = monthly["statement_month"].dt.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ #
    # 5. Cost-benefit: tighten credit-line increases on the high-risk segment
    # ------------------------------------------------------------------ #
    high_risk_mask = cur["risk_tier"].isin(["Tier 3 - Elevated", "Tier 4 - High Risk"])
    low_risk_mask = ~high_risk_mask

    high_risk_co_balance = cur.loc[high_risk_mask & (cur["charge_off_flag"] == 1), "ending_balance"].sum()
    high_risk_share_co = high_risk_co_balance / charge_off_balance if charge_off_balance else 0

    # Withholding CLI from the high-risk segment averts the CLI-driven growth share
    # of that segment's charge-off exposure. As a fraction of total charge-off
    # exposure, the reduction is the segment's share of charge-off balance times
    # the CLI growth share.
    chargeoff_exposure_reduction = high_risk_share_co * CLI_GROWTH_SHARE

    # Performing (non-charged-off) balance growth that stays eligible for CLI is
    # concentrated in the low-risk tiers, so most growth is preserved.
    performing = cur[cur["charge_off_flag"] == 0]
    low_risk_performing_share = (
        performing.loc[low_risk_mask, "ending_balance"].sum()
        / performing["ending_balance"].sum()
    )

    # ------------------------------------------------------------------ #
    # Findings dictionary
    # ------------------------------------------------------------------ #
    worst_grid = grid.sort_values("delinquency_rate", ascending=False).iloc[0]
    findings = {
        "accounts": int(n),
        "total_balance": round(float(total_balance), 2),
        "avg_utilization_pct": round(float(avg_util) * 100, 1),
        "portfolio_utilization_pct": round(float(portfolio_util) * 100, 1),
        "delinquency_rate_30plus_pct": round(float(delinquency_30) * 100, 2),
        "delinquency_rate_60plus_pct": round(float(delinquency_60) * 100, 2),
        "delinquency_rate_90plus_pct": round(float(delinquency_90) * 100, 2),
        "charge_off_rate_pct": round(float(charge_off_rate) * 100, 2),
        "charge_off_balance": round(float(charge_off_balance), 2),
        "delinquency_trend_start_pct": float(monthly["delinquency_rate"].iloc[0]),
        "delinquency_trend_end_pct": float(monthly["delinquency_rate"].iloc[-1]),
        "high_util_band_delinquency_pct": float(by_util.iloc[-1]["delinquency_rate"]),
        "thin_file_delinquency_pct": float(by_file.loc[by_file["thin_file"], "delinquency_rate"].iloc[0]),
        "established_file_delinquency_pct": float(by_file.loc[~by_file["thin_file"], "delinquency_rate"].iloc[0]),
        "worst_segment": worst_grid["segment"],
        "worst_segment_delinquency_pct": float(worst_grid["delinquency_rate"]),
        "top_driver": odds.iloc[0]["driver"],
        "top_driver_odds_ratio": float(odds.iloc[0]["odds_ratio_per_sd"]),
        "high_risk_tier_share_of_chargeoff_balance_pct": round(float(high_risk_share_co) * 100, 1),
        "cli_growth_share_assumption_pct": round(CLI_GROWTH_SHARE * 100, 0),
        "projected_chargeoff_exposure_reduction_pct": round(float(chargeoff_exposure_reduction) * 100, 1),
        "low_risk_performing_balance_share_pct": round(float(low_risk_performing_share) * 100, 1),
    }

    # ------------------------------------------------------------------ #
    # Write extracts for the BI tool
    # ------------------------------------------------------------------ #
    tier.to_csv(os.path.join(EXTRACT_DIR, "risk_tier_kpis.csv"), index=False)
    clus.to_csv(os.path.join(EXTRACT_DIR, "kmeans_segments.csv"), index=False)
    by_util.to_csv(os.path.join(EXTRACT_DIR, "delinquency_by_utilization.csv"), index=False)
    by_file.to_csv(os.path.join(EXTRACT_DIR, "delinquency_by_filedepth.csv"), index=False)
    grid.to_csv(os.path.join(EXTRACT_DIR, "delinquency_interaction.csv"), index=False)
    odds.to_csv(os.path.join(EXTRACT_DIR, "delinquency_drivers.csv"), index=False)
    monthly.to_csv(os.path.join(EXTRACT_DIR, "monthly_delinquency_trend.csv"), index=False)
    # Account-level scored extract (sampled) for dashboard scatter / drilldowns.
    cols = ["account_id", "risk_tier", "utilization", "fico_origination", "annual_income",
            "credit_history_months", "thin_file", "days_past_due", "ending_balance", "charge_off_flag"]
    cur_out = cur[cols].copy()
    cur_out["utilization"] = cur_out["utilization"].round(4)
    cur_out.sample(n=min(8000, len(cur_out)), random_state=42).to_csv(
        os.path.join(EXTRACT_DIR, "account_scored_sample.csv"), index=False)

    with open(os.path.join(REPORT_DIR, "kpis.json"), "w") as f:
        json.dump(findings, f, indent=2)

    write_findings_md(findings, tier, by_util, by_file, odds, clus)
    make_charts(tier, by_util, monthly, odds, grid)

    print("KPIs computed. Headline numbers:")
    for k in ["accounts", "delinquency_rate_30plus_pct", "charge_off_rate_pct",
              "avg_utilization_pct", "high_util_band_delinquency_pct",
              "thin_file_delinquency_pct", "top_driver",
              "projected_chargeoff_exposure_reduction_pct",
              "low_risk_performing_balance_share_pct"]:
        print(f"  {k:<46} {findings[k]}")
    print("\nExtracts -> dashboard/extracts/ | Charts -> dashboard/charts/ | KPIs -> reports/kpis.json")


def df_to_md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows = [
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |"
        for row in df.itertuples(index=False, name=None)
    ]
    return "\n".join([header, sep, *rows])


def write_findings_md(f, tier, by_util, by_file, odds, clus):
    lines = [
        "# Headline Findings",
        "",
        "_All figures are computed by `python/analytics.py` from the cleaned data._",
        "",
        "## Portfolio KPIs (latest cycle)",
        "",
        f"- Accounts: **{f['accounts']:,}**",
        f"- Total outstanding balance: **${f['total_balance']:,.0f}**",
        f"- Average utilization: **{f['avg_utilization_pct']}%**",
        f"- 30+ day delinquency rate: **{f['delinquency_rate_30plus_pct']}%**",
        f"- 60+ / 90+ day delinquency: **{f['delinquency_rate_60plus_pct']}% / {f['delinquency_rate_90plus_pct']}%**",
        f"- Charge-off rate: **{f['charge_off_rate_pct']}%** (balance: ${f['charge_off_balance']:,.0f})",
        f"- Delinquency trend over the window: **{f['delinquency_trend_start_pct']}% -> {f['delinquency_trend_end_pct']}%**",
        "",
        "## Risk-tier segmentation",
        "",
        df_to_md(tier[["risk_tier", "accounts", "pct_of_accounts", "avg_fico",
                       "avg_utilization", "pct_thin_file", "delinquency_rate",
                       "charge_off_rate", "pct_of_chargeoff_balance"]]),
        "",
        "## Unsupervised segments (KMeans)",
        "",
        df_to_md(clus[["segment", "accounts", "avg_utilization", "avg_fico",
                       "avg_income", "avg_history_months", "delinquency_rate"]]),
        "",
        "## Root cause: delinquency by utilization",
        "",
        df_to_md(by_util[["utilization_band", "accounts", "pct_of_accounts",
                          "delinquency_rate", "pct_of_all_delinquents"]]),
        "",
        "## Root cause: delinquency by credit-file depth",
        "",
        df_to_md(by_file[["file_depth", "accounts", "avg_history_months",
                          "delinquency_rate", "charge_off_rate"]]),
        "",
        "## Delinquency drivers (logistic regression, odds ratio per +1 SD)",
        "",
        df_to_md(odds),
        "",
        f"The strongest single driver is **{f['top_driver']}** "
        f"(odds ratio **{f['top_driver_odds_ratio']}x** per standard deviation): "
        "an account one standard deviation higher on this factor is that many times "
        "more likely to be 30+ days delinquent, holding the others constant.",
        "",
        "## Recommendation (cost-benefit)",
        "",
        f"- The high-risk tiers hold **{f['high_risk_tier_share_of_chargeoff_balance_pct']}%** "
        "of all charged-off balance.",
        f"- Withholding credit-line increases from that segment (assuming CLI-driven "
        f"growth accounts for ~{f['cli_growth_share_assumption_pct']:.0f}% of a "
        f"receiving account's charge-off exposure) projects a "
        f"**{f['projected_chargeoff_exposure_reduction_pct']}% reduction in charge-off exposure**.",
        f"- Low-risk tiers hold **{f['low_risk_performing_balance_share_pct']}%** of performing "
        "balance, so continuing CLI for them preserves the large majority of healthy growth.",
        "",
    ]
    with open(os.path.join(REPORT_DIR, "findings.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def make_charts(tier, by_util, monthly, odds, grid):
    plt.rcParams.update({"figure.dpi": 120, "axes.grid": True, "grid.alpha": 0.3})
    tier_colors = {"Tier 1 - Low Risk": "#27ae60", "Tier 2 - Moderate": "#f1c40f",
                   "Tier 3 - Elevated": "#e67e22", "Tier 4 - High Risk": "#c0392b"}

    # 1. Delinquency & charge-off by risk tier.
    fig, ax = plt.subplots(figsize=(9, 6))
    x = np.arange(len(tier))
    ax.bar(x - 0.2, tier["delinquency_rate"], width=0.4, label="30+ delinquency %", color="#2980b9")
    ax.bar(x + 0.2, tier["charge_off_rate"], width=0.4, label="charge-off %", color="#c0392b")
    ax.set_xticks(x)
    ax.set_xticklabels(tier["risk_tier"], rotation=20, ha="right")
    ax.set_ylabel("Rate (%)")
    ax.set_title("Delinquency & Charge-off by Risk Tier")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(CHART_DIR, "risk_tier_kpis.png"))
    plt.close(fig)

    # 2. Delinquency by utilization band (the headline driver).
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.bar(by_util["utilization_band"], by_util["delinquency_rate"], color="#8e44ad")
    ax.set_xlabel("Credit utilization band")
    ax.set_ylabel("30+ delinquency rate (%)")
    ax.set_title("Delinquency Rises Sharply with Utilization")
    fig.tight_layout()
    fig.savefig(os.path.join(CHART_DIR, "delinquency_by_utilization.png"))
    plt.close(fig)

    # 3. Monthly delinquency trend.
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(monthly["statement_month"], monthly["delinquency_rate"], marker="o", color="#c0392b", label="30+ delinquency %")
    ax.plot(monthly["statement_month"], monthly["charge_off_rate"], marker="s", color="#34495e", label="charge-off %")
    ax.set_xlabel("Statement month")
    ax.set_ylabel("Rate (%)")
    ax.set_title("Portfolio Delinquency & Charge-off Trend")
    ax.legend()
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(os.path.join(CHART_DIR, "delinquency_trend.png"))
    plt.close(fig)

    # 4. Driver odds ratios.
    fig, ax = plt.subplots(figsize=(8, 5))
    d = odds.sort_values("odds_ratio_per_sd")
    colors = ["#c0392b" if v > 1 else "#27ae60" for v in d["odds_ratio_per_sd"]]
    ax.barh(d["driver"], d["odds_ratio_per_sd"], color=colors)
    ax.axvline(1.0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Odds ratio per +1 SD (>1 = higher delinquency risk)")
    ax.set_title("Delinquency Drivers (logistic regression)")
    fig.tight_layout()
    fig.savefig(os.path.join(CHART_DIR, "delinquency_drivers.png"))
    plt.close(fig)


if __name__ == "__main__":
    main()
