-- =====================================================================
-- 01_cohort_assignment.sql
-- Digital Product AB Testing Analytics
--
-- Purpose : Build the user-level cohort (one row per user, the
--           randomization unit) from the raw event logs, and run the
--           assignment-integrity checks that must pass BEFORE any
--           effect is read: users in both variants, SRM, balance.
-- Input   : raw_experiment_logs (load data/raw_experiment_logs.csv
--           into a table with this name first)
-- Output  : user_cohort (view)
-- Dialect : Standard SQL (verified on DuckDB).
--
-- Rule: the randomization unit is the USER. All experiment metrics in
-- 02_metric_aggregation.sql are computed from user_cohort, never from
-- raw event counts.
-- =====================================================================


-- ---------------------------------------------------------------------
-- STEP 1 — user_cohort: one row per user.
-- variant/device/segment are taken with MIN() only AFTER the integrity
-- checks below confirm they never vary inside a user. converted = the
-- user produced a checkout_complete event in any of their sessions.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW user_cohort AS
SELECT
    user_id,
    MIN(variant)                                       AS variant,
    MIN(device_type)                                   AS device_type,
    MIN(user_segment)                                  AS user_segment,
    CAST(MIN(CAST(timestamp AS TIMESTAMP)) AS DATE)    AS assignment_date,
    COUNT(DISTINCT session_id)                         AS sessions,
    COUNT(*)                                           AS events,
    MAX(is_converted)                                  AS is_converted,
    MAX(time_spent_sec)                                AS session_duration_sec
FROM raw_experiment_logs
GROUP BY user_id;


-- ---------------------------------------------------------------------
-- STEP 2 — Integrity check 1: contamination.
-- Users (or sessions) appearing in BOTH variants. Expected: 0 and 0.
-- ---------------------------------------------------------------------
SELECT 'users_in_both_variants' AS check_name,
       COUNT(*) AS failed_rows
FROM (
    SELECT user_id
    FROM raw_experiment_logs
    GROUP BY user_id
    HAVING COUNT(DISTINCT variant) > 1
) c;

SELECT 'sessions_in_both_variants' AS check_name,
       COUNT(*) AS failed_rows
FROM (
    SELECT session_id
    FROM raw_experiment_logs
    GROUP BY session_id
    HAVING COUNT(DISTINCT variant) > 1
) c;

-- Attribute drift inside a user. Expected: 0.
SELECT 'user_attribute_drift' AS check_name,
       COUNT(*) AS failed_rows
FROM (
    SELECT user_id
    FROM raw_experiment_logs
    GROUP BY user_id
    HAVING COUNT(DISTINCT device_type) > 1
        OR COUNT(DISTINCT user_segment) > 1
) c;


-- ---------------------------------------------------------------------
-- STEP 3 — Integrity check 2: SRM (Sample Ratio Mismatch).
-- Observed split vs the 50/50 target. The query returns the counts and
-- the chi-square statistic; read the p-value in the notebook
-- (chi2 = 0.250 on this data, p = 0.617 -> no SRM).
-- ---------------------------------------------------------------------
SELECT
    variant,
    COUNT(*)                                        AS observed_users,
    SUM(COUNT(*)) OVER () / 2.0                     AS expected_users_50_50,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS observed_share_pct
FROM user_cohort
GROUP BY variant;

SELECT
    SUM(POWER(observed_users - expected_users, 2) / expected_users)
        AS chi_square_srm
FROM (
    SELECT
        COUNT(*)                    AS observed_users,
        SUM(COUNT(*)) OVER () / 2.0 AS expected_users
    FROM user_cohort
    GROUP BY variant
) split_counts;


-- ---------------------------------------------------------------------
-- STEP 4 — Integrity check 3: covariate balance.
-- Device and segment mix should look the same in both arms (the
-- chi-square tests on these tables are run in the notebook:
-- device p = 0.952, segment p = 0.206 -> balanced).
-- ---------------------------------------------------------------------
SELECT
    variant,
    device_type,
    COUNT(*)                                        AS users,
    ROUND(100.0 * COUNT(*)
          / SUM(COUNT(*)) OVER (PARTITION BY variant), 2)
                                                    AS share_within_variant_pct
FROM user_cohort
GROUP BY variant, device_type
ORDER BY device_type, variant;

SELECT
    variant,
    user_segment,
    COUNT(*)                                        AS users,
    ROUND(100.0 * COUNT(*)
          / SUM(COUNT(*)) OVER (PARTITION BY variant), 2)
                                                    AS share_within_variant_pct
FROM user_cohort
GROUP BY variant, user_segment
ORDER BY user_segment, variant;
