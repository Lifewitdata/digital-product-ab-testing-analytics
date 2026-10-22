-- =====================================================================
-- 02_metric_aggregation.sql
-- Digital Product AB Testing Analytics
--
-- Purpose : Aggregate user actions into the reporting tables used for
--           the experiment readout (run 01_cohort_assignment.sql
--           first — everything here reads user_cohort, the user-level
--           grain, plus one event-level funnel view).
-- Verified headline on the sample logs:
--   Control_A 205 users, 96 conversions, 46.83%
--   Variant_B 195 users, 98 conversions, 50.26%
--   Difference +3.43 pp (significance testing lives in the notebook —
--   SQL aggregates, it does not decide).
-- =====================================================================


-- ---------------------------------------------------------------------
-- TABLE 1 — experiment_scorecard: the primary readout per variant.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW experiment_scorecard AS
SELECT
    variant,
    COUNT(*)                                            AS users,
    SUM(is_converted)                                   AS conversions,
    ROUND(100.0 * SUM(is_converted) / COUNT(*), 2)      AS conversion_rate_pct,
    COUNT(*) - SUM(is_converted)                        AS drop_offs,
    ROUND(100.0 * (COUNT(*) - SUM(is_converted)) / COUNT(*), 2)
                                                        AS drop_off_rate_pct,
    ROUND(AVG(session_duration_sec), 1)                 AS avg_session_duration_sec,
    SUM(events)                                         AS total_events
FROM user_cohort
GROUP BY variant;


-- ---------------------------------------------------------------------
-- TABLE 2 — funnel_metrics: sessions reaching each event, by variant.
-- Every session in these logs has the same skeleton: page_view ->
-- checkout_start -> checkout_complete OR drop_off (event grain here,
-- counted in unique sessions).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW funnel_metrics AS
SELECT
    variant,
    COUNT(DISTINCT CASE WHEN event_name = 'page_view'
                        THEN session_id END)            AS sessions_page_view,
    COUNT(DISTINCT CASE WHEN event_name = 'checkout_start'
                        THEN session_id END)            AS sessions_checkout_start,
    COUNT(DISTINCT CASE WHEN event_name = 'checkout_complete'
                        THEN session_id END)            AS sessions_completed,
    COUNT(DISTINCT CASE WHEN event_name = 'drop_off'
                        THEN session_id END)            AS sessions_dropped_off
FROM raw_experiment_logs
GROUP BY variant;


-- ---------------------------------------------------------------------
-- TABLE 3 — daily_metrics: users, conversions and rate by day/variant.
-- For stability review and the no-peeking diagnostic — not for daily
-- ship decisions.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW daily_metrics AS
SELECT
    assignment_date,
    variant,
    COUNT(*)                                            AS users,
    SUM(is_converted)                                   AS conversions,
    ROUND(100.0 * SUM(is_converted) / COUNT(*), 2)      AS conversion_rate_pct
FROM user_cohort
GROUP BY assignment_date, variant;


-- ---------------------------------------------------------------------
-- TABLE 4 — device_metrics: the guardrail split.
-- Read this with its counts: Mobile is where Variant_B's point
-- estimate goes negative (37.80% -> 27.16%); Desktop carries the
-- positive signal (51.02% -> 65.93%). Formal interaction testing is
-- in the notebook.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW device_metrics AS
SELECT
    device_type,
    variant,
    COUNT(*)                                            AS users,
    SUM(is_converted)                                   AS conversions,
    ROUND(100.0 * SUM(is_converted) / COUNT(*), 2)      AS conversion_rate_pct
FROM user_cohort
GROUP BY device_type, variant;


-- ---------------------------------------------------------------------
-- TABLE 5 — segment_metrics: diagnostic split by user segment.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW segment_metrics AS
SELECT
    user_segment,
    variant,
    COUNT(*)                                            AS users,
    SUM(is_converted)                                   AS conversions,
    ROUND(100.0 * SUM(is_converted) / COUNT(*), 2)      AS conversion_rate_pct
FROM user_cohort
GROUP BY user_segment, variant;
