# Executive Summary — Credit Card Portfolio Performance & Delinquency Analytics

**Prepared by:** Athir Murad
**Scope:** 50,000 consumer credit-card accounts with 12 monthly statement cycles
each, across 4 card products and 4 regions (window ending May 2026).
**Every figure below is produced by the analysis pipeline** (`python/analytics.py`
and the queries in `sql/`); nothing is hand-entered. See
[`reports/findings.md`](findings.md) and [`reports/kpis.json`](kpis.json) for the
underlying values.

> The data is synthetic and generated for portfolio purposes, but the schema,
> metric definitions, and analytical methods are production-grade and would apply
> unchanged to a real card-processing extract.

---

## 1. Situation

Portfolio delinquency has been rising and leadership needs to know **why**, **who
is driving it**, and **what to do** without choking off healthy growth. The 30+
day delinquency rate climbed from **7.58%** to **13.32%** over the observation
window — a deteriorating trend, not a one-month blip.

## 2. Portfolio health

| KPI | Value |
| --- | --- |
| Accounts | **50,000** |
| Outstanding balance | **$298.9M** |
| Average utilization | **28.5%** |
| 30+ day delinquency rate | **15.37%** |
| 60+ / 90+ day delinquency | **6.91% / 4.70%** |
| Charge-off rate | **2.74%** ($11.2M balance) |

## 3. Key findings

**3.1 Risk is sharply concentrated.**
A rule-based segmentation places **37%** of accounts in the low-risk tier (5.98%
delinquency, near-zero charge-off) and just **6.9%** in the two highest-risk
tiers. Those high-risk tiers carry **78.2%** of all charged-off balance — a small
slice of accounts driving the large majority of loss. An unsupervised KMeans
segmentation independently reproduces the same gradient.

**3.2 High utilization is the dominant driver.**
Delinquency rises monotonically with credit utilization — from **5.9%** in the
under-30% band to **49%** at 60–90% and near-saturation above 90%. In a logistic
regression on standardized features, **utilization is the strongest driver, with
an odds ratio of 3.4× per standard deviation**, ahead of FICO and income.

**3.3 Thin-file accounts default at roughly twice the rate.**
Accounts with under 24 months of credit history run a **31.9%** delinquency rate
and a **12.6%** charge-off rate, versus **14.6%** and **2.3%** for established
files. The two drivers compound: the **high-utilization _and_ thin-file** segment
delinquency is **78.4%**, versus **9.6%** for low-utilization established files.

**3.4 The deterioration is broad-based, not a single vintage.**
Delinquency by origination vintage is flat (~15–16% across 2018–2025), so the
rising trend reflects a portfolio-wide stress ramp rather than one bad cohort —
consistent with a macro/affordability driver acting on already high-risk
segments.

## 4. Recommendation (cost-benefit)

**Tighten credit-line increases (CLI) for the high-risk segment (Tiers 3–4) while
continuing them for low-risk tiers.**

- The high-risk segment holds **78.2%** of charged-off balance. Assuming
  CLI-driven balance growth accounts for ~**19%** of a receiving account's
  eventual charge-off exposure, withholding CLI from this segment projects a
  **~15% reduction in charge-off exposure**.
- The low-risk tiers hold **94.3%** of performing balance, so continuing CLI for
  them **preserves 90%+ of healthy portfolio growth**. The tightening is surgical:
  it targets the 6.9% of accounts responsible for most loss, not the book at
  large.

*The CLI growth-share input is a stated planning assumption; the segment shares,
delinquency rates, and balance concentrations are taken directly from the data.*

### Supporting actions
1. **Gate CLI eligibility on utilization and file depth**, not FICO alone —
   utilization is the stronger predictor here.
2. **Add early-warning monitoring** on accounts crossing 60% utilization, where
   delinquency begins to climb steeply.
3. **Apply graduated limits to thin-file originations** until they season past
   24 months of history.

## 5. How to reproduce

```
python python/generate_raw_data.py     # synthetic source files
python python/clean_validate.py        # clean + validate -> data/processed
python python/analytics.py             # KPIs, segments, drivers, this summary's numbers
# then load + query in PostgreSQL: see README and sql/
```
