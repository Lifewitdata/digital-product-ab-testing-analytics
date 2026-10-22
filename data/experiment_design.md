# Experiment Design — Checkout A/B Test (Control_A vs Variant_B)

> **Source note.** This document is reconstructed from
> `data/raw_experiment_logs.csv`, the only record shipped with the logs.
> Everything under *Observed in the logs* is verified from the data.
> The exact UI/experience change that defines Variant_B is **not recorded
> in the logs** — add that one line here before this document is cited
> outside the project. No pre-registration record accompanies the logs.

## Test parameters (observed in the logs)

| Parameter | Value |
|---|---|
| Experiment surface | Checkout flow: `/checkout` → `/checkout-step1` → `/checkout-confirmation` |
| Control | `Control_A` — existing checkout experience |
| Variant | `Variant_B` — checkout variant under test (exact change not in logs) |
| Randomization unit | **User** (`user_id`) — each user appears in exactly one variant |
| Observed split | Control_A 205 users (51.25%) / Variant_B 195 users (48.75%), consistent with a 50/50 target |
| Eligibility | Users with a `page_view` on `/checkout` (all 400 logged users qualify) |
| Run window | 10–23 March 2026 (14 complete days) |
| Sample in logs | 400 users, 400 sessions, 1,200 events (exactly 3 events per session) |
| Assignment integrity | No user in both variants; no variant, device, or segment drift inside a session (verified) |

## Event schema of the test

Every session follows the same 3-event skeleton:

| Step | `event_name` | `page_location` | Meaning |
|---|---|---|---|
| 1 | `page_view` | `/checkout` | User enters checkout (assignment observed here) |
| 2 | `checkout_start` | `/checkout-step1` | User starts checkout step 1 (all 400 sessions reach this) |
| 3a | `checkout_complete` | `/checkout-confirmation` | Conversion — `is_converted = 1` |
| 3b | `drop_off` | `/checkout-step1` | Session abandoned at step 1 — `is_converted = 0` |

`time_spent_sec` is the elapsed seconds in the session when the event fired
(page_view ≈ 10s, checkout_start ≈ 25s, terminal event ≈ 35–45s).

## Hypotheses

- **H1 (primary):** Variant_B changes the user-level checkout completion
  rate versus Control_A.
  - H0: completion rate B = completion rate A
  - H1: completion rate B ≠ completion rate A (two-sided)
- **H2 (guardrail):** Variant_B does not materially change time in checkout
  (session duration) and does not harm any device cohort — in particular
  Mobile, the largest friction cohort in the funnel analyses.

## Metrics

| Role | Metric | Definition |
|---|---|---|
| Primary | Checkout completion rate (user level) | users with a `checkout_complete` event ÷ users assigned |
| Secondary | Drop-off rate | users ending in `drop_off` ÷ users assigned (= 1 − primary here) |
| Secondary | Session duration | `MAX(time_spent_sec)` per session, seconds |
| Guardrail | Completion rate by device | primary metric split by `device_type` |
| Diagnostic | Completion rate by segment | primary metric split by `user_segment` |
| Diagnostic | Daily completion rate | primary metric by assignment date (stability / peeking check) |

## Analysis plan (as executed in `python/ab_test_significance.ipynb`)

1. **Assignment integrity first:** duplicate check, users-in-both-variants
   check, SRM chi-square test against the 50/50 target, and covariate
   balance tests (device, segment) across arms. No effect is read before
   these pass.
2. **Primary test:** two-proportion z-test (two-sided, α = 0.05), reported
   with the absolute difference, relative lift, and a 95% confidence
   interval. Chi-square (same table) and Fisher's exact test as robustness.
3. **Power accounting:** minimum detectable effect at the achieved sample,
   power for the observed effect, and the per-arm sample size the observed
   effect would require — so an inconclusive result is labelled
   inconclusive, not negative.
4. **Heterogeneity:** per-device and per-segment differences with CIs,
   Bonferroni correction across the device family, and a logistic-regression
   variant × device interaction test.
5. **No-peeking discipline:** cumulative daily readouts are computed for
   diagnostics only; the decision uses the final readout alone.
6. **Bayesian cross-check:** Beta-posterior P(B > A) and expected loss of
   each ship / don't-ship choice.

## Decision rule applied

Ship only if **all** hold: (a) primary effect significant at α = 0.05 with
the CI excluding a practically meaningful harm, (b) no device cohort shows
a credible negative effect, (c) the duration guardrail is neutral.

**Outcome on this run: NOT MET → do not ship; result is inconclusive, not
negative.** Primary effect +3.43 pp (p = 0.493, 95% CI −6.37 to +13.22 pp)
at only ~10% power for an effect of that size, with a significant
variant × device interaction (Mobile point estimate −10.6 pp). Full
reasoning in the notebook, Section 15, and the README.

## Design requirements for the follow-up run

- Power for a **+5 pp** target effect at the observed 46.8% baseline:
  **≈ 1,566 users per arm** (≈ 3,132 total) for 80% power, two-sided
  α = 0.05. (For the observed +3.43 pp: ≈ 3,335 per arm.)
- Debug the Mobile Variant_B experience **before** re-running — the
  interaction signal (p = 0.014) says the variant behaves differently
  there.
- Pre-register device as a subgroup and fix the runtime (whole weeks) in
  advance; read once, at the end.
