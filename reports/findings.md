# Headline Findings

_All figures are computed by `python/analytics.py` from the cleaned data._

## Portfolio KPIs (latest cycle)

- Accounts: **50,000**
- Total outstanding balance: **$298,923,919**
- Average utilization: **28.5%**
- 30+ day delinquency rate: **15.37%**
- 60+ / 90+ day delinquency: **6.91% / 4.7%**
- Charge-off rate: **2.74%** (balance: $11,181,316)
- Delinquency trend over the window: **7.58% -> 13.32%**

## Risk-tier segmentation

| risk_tier | accounts | pct_of_accounts | avg_fico | avg_utilization | pct_thin_file | delinquency_rate | charge_off_rate | pct_of_chargeoff_balance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Tier 1 - Low Risk | 18526 | 37.1 | 766.0 | 20.94 | 0.0 | 5.98 | 0.03 | 0.5 |
| Tier 2 - Moderate | 28059 | 56.1 | 674.0 | 27.81 | 4.97 | 14.82 | 0.72 | 21.2 |
| Tier 3 - Elevated | 2328 | 4.7 | 621.0 | 67.1 | 21.13 | 59.41 | 16.97 | 29.6 |
| Tier 4 - High Risk | 1087 | 2.2 | 601.0 | 93.8 | 36.98 | 95.31 | 70.65 | 48.6 |

## Unsupervised segments (KMeans)

| segment | accounts | avg_utilization | avg_fico | avg_income | avg_history_months | delinquency_rate |
| --- | --- | --- | --- | --- | --- | --- |
| Segment A - Lowest risk | 12265 | 21.3 | 765.0 | 93584.0 | 152.0 | 5.98 |
| Segment B - Moderate | 12167 | 25.0 | 712.0 | 56651.0 | 337.0 | 9.91 |
| Segment C - Elevated | 23179 | 28.6 | 677.0 | 47210.0 | 107.0 | 14.48 |
| Segment D - Highest risk | 2389 | 82.7 | 620.0 | 41196.0 | 137.0 | 100.0 |

## Root cause: delinquency by utilization

| utilization_band | accounts | pct_of_accounts | delinquency_rate | pct_of_all_delinquents |
| --- | --- | --- | --- | --- |
| <30% | 28221 | 56.4 | 5.86 | 21.5 |
| 30-60% | 16059 | 32.1 | 16.71 | 34.9 |
| 60-90% | 4632 | 9.3 | 48.99 | 29.5 |
| 90%+ | 1088 | 2.2 | 98.9 | 14.0 |

## Root cause: delinquency by credit-file depth

| file_depth | accounts | avg_history_months | delinquency_rate | charge_off_rate |
| --- | --- | --- | --- | --- |
| Established file | 47712 | 183.0 | 14.57 | 2.27 |
| Thin file (<24 mo) | 2288 | 16.0 | 31.91 | 12.63 |

## Delinquency drivers (logistic regression, odds ratio per +1 SD)

| driver | coef_per_sd | odds_ratio_per_sd |
| --- | --- | --- |
| utilization | 1.224 | 3.4 |
| thin_file | 0.109 | 1.12 |
| age | -0.001 | 1.0 |
| log_income | -0.13 | 0.88 |
| fico_origination | -0.477 | 0.62 |

The strongest single driver is **utilization** (odds ratio **3.4x** per standard deviation): an account one standard deviation higher on this factor is that many times more likely to be 30+ days delinquent, holding the others constant.

## Recommendation (cost-benefit)

- The high-risk tiers hold **78.2%** of all charged-off balance.
- Withholding credit-line increases from that segment (assuming CLI-driven growth accounts for ~19% of a receiving account's charge-off exposure) projects a **14.9% reduction in charge-off exposure**.
- Low-risk tiers hold **94.3%** of performing balance, so continuing CLI for them preserves the large majority of healthy growth.
