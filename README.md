# Digital Product A/B Testing Analytics

End-to-end analysis of a checkout A/B test (`Control_A` vs `Variant_B`):
assignment integrity, significance testing, power accounting, device and
segment heterogeneity, and a ship / no-ship decision — from raw interaction
logs to a defensible readout.

**Verdict: DO NOT SHIP — inconclusive, with a live Mobile concern.**
Variant_B's headline effect is +3.43 pp (50.26% vs 46.83%), but it is not
significant (p = 0.493, 95% CI −6.37 to +13.22 pp), the run had only ~10%
power for an effect that size, and a significant variant × device
interaction (p = 0.014) shows the variant behaving *oppositely* on Mobile
(−10.64 pp point estimate) versus Desktop (+14.91 pp).

## Experiment at a glance

| | |
|---|---|
| Surface | Checkout: `/checkout` → `/checkout-step1` → `/checkout-confirmation` |
| Randomization unit | User — 400 users, 1 session each, 1,200 events |
| Split | Control_A 205 (51.25%) / Variant_B 195 (48.75%), 50/50 target |
| Window | 10–23 March 2026 (14 days) |
| Primary metric | User-level checkout completion rate |

## Verified results

### Assignment integrity — all pass

| Check | Result |
|---|---|
| Users in both variants | 0 |
| Duplicates / attribute drift | 0 / 0 |
| SRM chi-square | 0.250, p = 0.617 (no mismatch) |
| Covariate balance | device p = 0.952, segment p = 0.206 |

### Primary readout

| Variant | Users | Conversions | Rate |
|---|---|---|---|
| Control_A | 205 | 96 | 46.83% |
| Variant_B | 195 | 98 | 50.26% |
| **Difference** | | | **+3.43 pp** (relative +7.32%) |

Two-proportion z-test z = 0.686, **p = 0.493** · chi-square p = 0.493 ·
Fisher p = 0.548 · 95% CI **−6.37 to +13.22 pp**.

### Power accounting

| Question | Answer |
|---|---|
| Power of this run for the observed +3.43 pp | ~10% |
| Minimum detectable effect at 80% power, this n | ~14 pp |
| Users/arm needed for +3.43 pp | ~3,335 |
| Users/arm needed for +5 pp (recommended re-run target) | ~1,566 |

### Heterogeneity — the real story

| Device | Control_A | Variant_B | Difference | p |
|---|---|---|---|---|
| Desktop | 51.02% (98) | 65.93% (91) | +14.91 pp | 0.035* |
| Tablet | 60.00% (25) | 69.57% (23) | +9.57 pp | 0.485 |
| Mobile | 37.80% (82) | 27.16% (81) | **−10.64 pp** | 0.144 |

*Desktop does not survive Bonferroni correction across the 3 device tests
(threshold 0.0167), but the logistic **variant × Mobile interaction is
significant (p = 0.014)** — the treatment effect genuinely differs by
device. Segments show nothing (all p ≥ 0.47, interactions p ≥ 0.81).

### What `verify_analysis.py` proves

`python verify_analysis.py` recomputes this page from the committed CSV and
exits non-zero if anything disagrees. It runs 350 checks in 13 sections:
the integrity claims above, the two SQL files executed on DuckDB and tied
cell-by-cell to the pandas cohort, every statistic in the notebook
re-derived from scratch, the stored notebook outputs diffed against fresh
recomputation, the prose numbers on this page and in
`data/experiment_design.md` string-matched against those values, and the
repo's own file tree.

The variant × device interaction is the one headline that the notebook
produces with statsmodels — which is not installed everywhere. The harness
refits the same logistic model with its own Newton-Raphson IRLS
(`logit_fit`) and recovers the stored coefficients and p-values to three
decimal places, so `p = 0.014` does not depend on statsmodels being
present. The variant × segment interaction quoted above is not in the
notebook at all; the harness computes it (p = 0.81 and 0.91, i.e. flat).

### Guardrails and cross-checks

- Session duration: 40.0s (A) vs 40.5s (B), Welch p = 0.612 — neutral.
- Funnel: 100% of sessions in both arms reach `checkout_start`; the whole
  test plays out at the step-1 complete-vs-drop_off branch.
- Peeking diagnostic: the cumulative p-value never approaches 0.05 — its low
  point is day 1 (0.298, at n = 20 vs 9), and the last three readouts sit at
  0.42–0.49.
- Bayesian: P(B > A) = 75.3%, expected loss of shipping B = 0.73 pp —
  softer, but pooled across devices, so it does not override the
  interaction signal.

## Repository structure

```
digital-product-ab-testing-analytics/
├── data/
│   ├── raw_experiment_logs.csv     # anonymized interaction logs (1,200 events, synthetic)
│   └── experiment_design.md        # test parameters, hypotheses, analysis plan, decision rule
├── sql/
│   ├── 01_cohort_assignment.sql    # user cohort + contamination / SRM / balance checks
│   └── 02_metric_aggregation.sql   # scorecard, funnel, daily, device, segment tables
├── python/
│   ├── ab_test_significance.ipynb  # executed in-depth analysis (this project's core)
│   ├── exploratory_visuals.py      # generates the chart pack below
│   └── charts/                     # 6 presentation-ready PNGs
├── verify_analysis.py              # recomputes and cross-checks every published figure
├── .github/workflows/ci.yml        # runs verify_analysis.py + the chart pack on every push
├── requirements.txt
├── LICENSE
└── README.md
```

## How to run

```bash
pip install -r requirements.txt

# Recompute and cross-check every figure on this page  -> "ALL PASS", exit 0
python verify_analysis.py

# Notebook (outputs and charts already embedded)
#   python/ab_test_significance.ipynb
#   It locates data/raw_experiment_logs.csv from the repo root or from python/,
#   so it runs whichever folder the kernel starts in.

# Chart pack
python python/exploratory_visuals.py   # rewrites python/charts/*.png

# SQL (any standard-SQL engine; verify_analysis.py runs both files on DuckDB)
#   load data/raw_experiment_logs.csv as table raw_experiment_logs, then:
#   sql/01_cohort_assignment.sql  -> user_cohort + integrity checks
#   sql/02_metric_aggregation.sql -> experiment_scorecard, funnel_metrics,
#                                    daily_metrics, device_metrics, segment_metrics
```

## Data provenance

`data/raw_experiment_logs.csv` is a **synthetic, anonymized event log**, not
production telemetry. Its structure gives it away: 400 consecutive user ids
(`USR_2001`–`USR_2400`), each session id derived from its user
(`USR_2172` → `SES_8172`), and exactly 3 events per session with no partial
sessions at the window edges. The analysis is therefore a complete,
reproducible worked example of A/B methodology — every number on this page
is arithmetic on this one file, provable by running `verify_analysis.py`.
Nothing here should be cited as evidence about a real checkout product.
`data/experiment_design.md` explains what the logs do and do not record
(including that they never say what Variant_B changed).

## Recommended next steps

1. **Debug Variant_B on Mobile before any re-run** — the interaction says
   the variant's mobile step-1 experience behaves differently; find out why.
2. **Re-run pre-registered:** primary metric fixed in advance, +5 pp target
   effect, ~1,566 users per arm, whole-week runtime, device declared as a
   pre-specified subgroup, single end-of-run readout.
3. **Do not extend this run** and re-read it later — that converts a clean
   inconclusive result into a peeked one.

## Limitations

- 400 users over 14 days — wide intervals throughout; Tablet cells are
  n = 48 combined.
- One session per user: no repeat-visit, retention, or revenue readout is
  possible from these logs.
- The logs do not record what Variant_B changed, so mechanisms behind the
  device interaction cannot be proven from telemetry alone
  (`experiment_design.md` flags this explicitly).
