# Data Quality Report

_Generated: 2026-06-26 18:45_

This report documents every cleaning and validation action applied when
transforming the raw card-system exports into analysis-ready tables.

## `regions`

| Check | Count |
| --- | ---: |
| rows out | 4 |

## `card_products`

| Check | Count |
| --- | ---: |
| rows out | 4 |

## `cardholders`

| Check | Count |
| --- | ---: |
| exact duplicates removed | 0 |
| missing income imputed | 1532 |
| impossible ages fixed | 40 |
| employment blanks filled | 2542 |
| fico out of range clipped | 0 |
| rows out | 50000 |
| orphan region fk dropped | 0 |

## `accounts`

| Check | Count |
| --- | ---: |
| duplicate accounts removed | 25 |
| nonpositive limit dropped | 0 |
| rows out | 50000 |
| orphan fk dropped | 0 |

## `monthly_statements`

| Check | Count |
| --- | ---: |
| exact duplicates removed | 50 |
| negative payments corrected | 182 |
| missing payments zeroed | 300 |
| rows out | 595253 |
| orphan account fk dropped | 0 |
