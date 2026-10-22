"""
verify_analysis.py
Digital Product A/B Testing Analytics

Recomputes every figure published in this repo, from the committed CSV, and
proves three things agree with each other:

  1. the SQL in sql/ (run here on DuckDB)  ==  the pandas in the notebook,
  2. the stored notebook outputs            ==  fresh recomputation,
  3. the numbers written in README.md and
     data/experiment_design.md              ==  those values.

The logistic interaction terms are fitted here with a dependency-free
Newton-Raphson IRLS, so the variant x Mobile claim (coef -1.108, p 0.014,
which the notebook obtains from statsmodels) is verified without needing
statsmodels installed.

Run:  python verify_analysis.py        (exit code 0 = everything agrees)
"""
from pathlib import Path

import json
import re

import numpy as np
import pandas as pd
import sqlglot
from scipy import stats

BASE = Path(__file__).resolve().parent
LOGS = BASE / "data" / "raw_experiment_logs.csv"

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}" + (f"   [{detail}]" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"   [{detail}]" if detail else ""))


def close(label, got, want, tol=5e-4):
    check(label, bool(np.isclose(got, want, rtol=0, atol=tol)), f"{got:g} vs {want:g}")


def section(title):
    print(f"\n{title}")


# ------------------------------------------------------------------ load
df = pd.read_csv(LOGS, parse_dates=["timestamp"])

users = df.groupby("user_id").agg(
    variant=("variant", "first"),
    device_type=("device_type", "first"),
    user_segment=("user_segment", "first"),
    converted=("is_converted", "max"),
    session_duration_sec=("time_spent_sec", "max"),
    first_seen=("timestamp", "min"),
).reset_index()
users["assignment_date"] = users["first_seen"].dt.date

A = users[users["variant"] == "Control_A"]
B = users[users["variant"] == "Variant_B"]

section("1. Raw log shape")
check("1,200 events", len(df) == 1200, f"{len(df)}")
check("400 users", df["user_id"].nunique() == 400, f"{df['user_id'].nunique()}")
check("400 sessions", df["session_id"].nunique() == 400, f"{df['session_id'].nunique()}")
check("one session per user", (df.groupby("user_id")["session_id"].nunique() == 1).all())
check("no null cells", int(df.isna().sum().sum()) == 0)
check("no duplicate rows", int(df.duplicated().sum()) == 0)
check("window starts 2026-03-10", str(df["timestamp"].min()) == "2026-03-10 08:23:53",
      str(df["timestamp"].min()))
check("window ends 2026-03-23", str(df["timestamp"].max()) == "2026-03-23 22:19:27",
      str(df["timestamp"].max()))
check("14 calendar days covered",
      df["timestamp"].dt.normalize().nunique() == 14,
      f"{df['timestamp'].dt.normalize().nunique()} days")
check("user cohort is 400 rows", len(users) == 400, f"{len(users)}")

section("2. Event schema and assignment integrity")
ev = df["event_name"].value_counts()
check("400 page_view events", ev["page_view"] == 400, f"{ev['page_view']}")
check("400 checkout_start events", ev["checkout_start"] == 400, f"{ev['checkout_start']}")
check("194 checkout_complete events", ev["checkout_complete"] == 194, f"{ev['checkout_complete']}")
check("206 drop_off events", ev["drop_off"] == 206, f"{ev['drop_off']}")
check("3 events per session everywhere",
      (df.groupby("session_id").size() == 3).all())
loc = pd.crosstab(df["event_name"], df["page_location"])
check("page_view only on /checkout",
      int(loc.loc["page_view", "/checkout"]) == 400
      and int(loc.loc["page_view", ["/checkout-confirmation", "/checkout-step1"]].sum()) == 0)
check("checkout_start only on /checkout-step1", loc.loc["checkout_start", "/checkout-step1"] == 400
      and loc.loc["checkout_start", ["/checkout", "/checkout-confirmation"]].sum() == 0)
check("checkout_complete only on /checkout-confirmation",
      loc.loc["checkout_complete", "/checkout-confirmation"] == 194
      and loc.loc["checkout_complete", ["/checkout", "/checkout-step1"]].sum() == 0)
check("drop_off only on /checkout-step1", loc.loc["drop_off", "/checkout-step1"] == 206
      and loc.loc["drop_off", ["/checkout", "/checkout-confirmation"]].sum() == 0)
check("no user in both variants",
      int((df.groupby("user_id")["variant"].nunique() > 1).sum()) == 0)
check("no session in both variants",
      int((df.groupby("session_id")["variant"].nunique() > 1).sum()) == 0)
check("no device drift inside a session",
      int((df.groupby("session_id")["device_type"].nunique() > 1).sum()) == 0)
check("no segment drift inside a session",
      int((df.groupby("session_id")["user_segment"].nunique() > 1).sum()) == 0)
check("is_converted = 1 only on checkout_complete",
      set(df.loc[df["is_converted"] == 1, "event_name"]) == {"checkout_complete"})
check("every checkout_complete carries is_converted = 1",
      int(((df["event_name"] == "checkout_complete") & (df["is_converted"] == 0)).sum()) == 0)
check("converted users == 194 conversions", int(users["converted"].sum()) == 194,
      f"{users['converted'].sum()}")
check("split is 205 / 195", len(A) == 205 and len(B) == 195, f"{len(A)} / {len(B)}")

section("3. SQL (DuckDB) vs pandas")
import duckdb  # noqa: E402  (heavy import, only needed for this section)

con = duckdb.connect()
# read_csv_auto, not the pandas frame: DuckDB infers the schema independently,
# so this section compares two parsers as well as two aggregation paths.
csv_sql = str(LOGS).replace("\\", "/").replace("'", "''")
con.execute(f"CREATE TABLE raw_experiment_logs AS SELECT * FROM read_csv_auto('{csv_sql}')")

VIEW_COLS = {}
for name in ("01_cohort_assignment.sql", "02_metric_aggregation.sql"):
    script = (BASE / "sql" / name).read_text(encoding="utf-8")
    for stmt in sqlglot.parse(script, read="duckdb"):
        if stmt is None:
            continue
        con.execute(stmt.sql(dialect="duckdb"))
    check(f"sql/{name} executes on DuckDB", True)

user_cohort = con.execute("SELECT * FROM user_cohort").fetchdf()
check("user_cohort has 400 rows", len(user_cohort) == 400, f"{len(user_cohort)}")
check("user_cohort grain is one row per user",
      user_cohort["user_id"].nunique() == len(user_cohort))
check("user_cohort variant counts match pandas",
      sorted(user_cohort["variant"].value_counts().tolist()) == sorted([len(A), len(B)]))
check("user_cohort conversions match pandas",
      int(user_cohort["is_converted"].sum()) == int(users["converted"].sum()))
check("user_cohort durations match pandas",
      user_cohort.set_index("user_id")["session_duration_sec"].sort_index().tolist()
      == users.set_index("user_id")["session_duration_sec"].sort_index().tolist())
check("user_cohort assignment dates match pandas",
      pd.to_datetime(user_cohort.set_index("user_id")["assignment_date"]).sort_index().dt.date.tolist()
      == users.set_index("user_id")["assignment_date"].sort_index().tolist())

sc = con.execute("SELECT * FROM experiment_scorecard ORDER BY variant").fetchdf()
sc_p = users.groupby("variant").agg(n=("user_id", "count"), k=("converted", "sum"))
for _, row in sc.iterrows():
    v = row["variant"]
    check(f"scorecard {v} users", int(row["users"]) == int(sc_p.loc[v, "n"]))
    check(f"scorecard {v} conversions", int(row["conversions"]) == int(sc_p.loc[v, "k"]))
    close(f"scorecard {v} rate_pct", float(row["conversion_rate_pct"]),
          100 * sc_p.loc[v, "k"] / sc_p.loc[v, "n"], tol=0.005)
    check(f"scorecard {v} drop_offs = users - conversions",
          int(row["drop_offs"]) == int(row["users"]) - int(row["conversions"]))
    close(f"scorecard {v} drop_off_rate_pct", float(row["drop_off_rate_pct"]),
          100 - float(row["conversion_rate_pct"]), tol=0.005)
    close(f"scorecard {v} avg duration", float(row["avg_session_duration_sec"]),
          users.loc[users["variant"] == v, "session_duration_sec"].mean(), tol=0.05)
    check(f"scorecard {v} total_events", int(row["total_events"]) ==
          int(users.loc[users["variant"] == v].shape[0]) * 3)
check("scorecard event total is 1,200", int(sc["total_events"].sum()) == 1200)

fn = con.execute("SELECT * FROM funnel_metrics ORDER BY variant").fetchdf()
fn_p = df.groupby(["variant", "event_name"])["session_id"].nunique()
for _, row in fn.iterrows():
    v = row["variant"]
    check(f"funnel {v} page_view", int(row["sessions_page_view"]) == int(fn_p[v, "page_view"]))
    check(f"funnel {v} checkout_start",
          int(row["sessions_checkout_start"]) == int(fn_p[v, "checkout_start"]))
    check(f"funnel {v} completed", int(row["sessions_completed"]) == int(fn_p[v, "checkout_complete"]))
    check(f"funnel {v} dropped_off", int(row["sessions_dropped_off"]) == int(fn_p[v, "drop_off"]))
    check(f"funnel {v}: completed + dropped = all sessions",
          int(row["sessions_completed"]) + int(row["sessions_dropped_off"])
          == int(row["sessions_page_view"]))
    check(f"funnel {v}: every session starts checkout",
          int(row["sessions_checkout_start"]) == int(row["sessions_page_view"]))

dm = con.execute("SELECT * FROM daily_metrics").fetchdf()
check("daily_metrics is 14 days x 2 variants", len(dm) == 28, f"{len(dm)} rows")
day_p = users.groupby(["assignment_date", "variant"]).agg(n=("user_id", "count"),
                                                          k=("converted", "sum"))
for _, row in dm.iterrows():
    key = (pd.Timestamp(row["assignment_date"]).date(), row["variant"])
    if key not in day_p.index:
        check(f"daily {key}", False, "missing in pandas")
        continue
    check(f"daily {key[0]} {key[1]} users", int(row["users"]) == int(day_p.loc[key, "n"]))
    check(f"daily {key[0]} {key[1]} conversions",
          int(row["conversions"]) == int(day_p.loc[key, "k"]))
check("daily totals reconcile to 400 users", int(dm["users"].sum()) == 400)
check("daily totals reconcile to 194 conversions", int(dm["conversions"].sum()) == 194)

dev = con.execute("SELECT * FROM device_metrics").fetchdf()
dev_p = users.groupby(["device_type", "variant"]).agg(n=("user_id", "count"), k=("converted", "sum"))
for _, row in dev.iterrows():
    key = (row["device_type"], row["variant"])
    check(f"device_metrics {key}",
          int(row["users"]) == int(dev_p.loc[key, "n"])
          and int(row["conversions"]) == int(dev_p.loc[key, "k"])
          and np.isclose(float(row["conversion_rate_pct"]),
                         100 * dev_p.loc[key, "k"] / dev_p.loc[key, "n"], atol=0.005))
seg = con.execute("SELECT * FROM segment_metrics").fetchdf()
seg_p = users.groupby(["user_segment", "variant"]).agg(n=("user_id", "count"), k=("converted", "sum"))
for _, row in seg.iterrows():
    key = (row["user_segment"], row["variant"])
    check(f"segment_metrics {key}",
          int(row["users"]) == int(seg_p.loc[key, "n"])
          and int(row["conversions"]) == int(seg_p.loc[key, "k"])
          and np.isclose(float(row["conversion_rate_pct"]),
                         100 * seg_p.loc[key, "k"] / seg_p.loc[key, "n"], atol=0.005))
con.close()

section("4. SRM and covariate balance")
observed = np.array([len(A), len(B)])
expected = np.array([len(users) / 2, len(users) / 2])
chi2_srm = float(((observed - expected) ** 2 / expected).sum())
p_srm = float(stats.chi2.sf(chi2_srm, df=1))
close("SRM chi-square", chi2_srm, 0.250)
close("SRM p-value", p_srm, 0.6171, tol=5e-4)
check("no SRM at the p < 0.001 convention", p_srm > 0.001, f"p = {p_srm:.4f}")
chi2_dev, p_dev, _, _ = stats.chi2_contingency(pd.crosstab(users["variant"], users["device_type"]))
chi2_seg, p_seg, _, _ = stats.chi2_contingency(pd.crosstab(users["variant"], users["user_segment"]))
close("device balance chi-square", float(chi2_dev), 0.099, tol=5e-4)
close("device balance p", float(p_dev), 0.952, tol=5e-4)
close("segment balance chi-square", float(chi2_seg), 3.161, tol=5e-4)
close("segment balance p", float(p_seg), 0.206, tol=5e-4)
check("both balance tests pass at 0.05", p_dev > 0.05 and p_seg > 0.05)
check("reported bound: balance p >= 0.20", min(p_dev, p_seg) >= 0.20,
      f"min = {min(p_dev, p_seg):.3f}")

section("5. Primary metric")
p_a = float(A["converted"].mean())
p_b = float(B["converted"].mean())
n_a, n_b = len(A), len(B)
diff_pp = (p_b - p_a) * 100
rel_lift = (p_b - p_a) / p_a * 100
close("Control_A rate %", p_a * 100, 46.8293, tol=5e-4)
close("Variant_B rate %", p_b * 100, 50.2564, tol=5e-4)
close("absolute difference pp", diff_pp, 3.4271, tol=5e-4)
close("relative lift %", rel_lift, 7.3187, tol=5e-4)
check("Control_A is 96/205", int(A["converted"].sum()) == 96 and n_a == 205)
check("Variant_B is 98/195", int(B["converted"].sum()) == 98 and n_b == 195)
se_diff = float(np.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b))
ci_low = diff_pp - 1.96 * se_diff * 100
ci_high = diff_pp + 1.96 * se_diff * 100
check("95% CI low is the published -6.37 pp", round(ci_low, 2) == -6.37, f"{ci_low:.4f}")
check("95% CI high is the published +13.22 pp", round(ci_high, 2) == 13.22, f"{ci_high:.4f}")
check("CI includes zero -> not significant", ci_low < 0 < ci_high)

p_pool = float(users["converted"].mean())
se_pool = float(np.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b)))
z_stat = (p_b - p_a) / se_pool
p_z = float(2 * stats.norm.sf(abs(z_stat)))
close("pooled z statistic", z_stat, 0.6858, tol=5e-4)
close("two-sided z p-value", p_z, 0.4930, tol=5e-4)
table = np.array([[int(A["converted"].sum()), n_a - int(A["converted"].sum())],
                  [int(B["converted"].sum()), n_b - int(B["converted"].sum())]])
chi2_prim, p_chi, _, _ = stats.chi2_contingency(table, correction=False)
close("chi-square equals z squared", float(chi2_prim), z_stat ** 2, tol=1e-4)
close("chi-square p equals z p", float(p_chi), p_z, tol=1e-6)
odds, p_fisher = stats.fisher_exact(table)
close("Fisher p-value", float(p_fisher), 0.5483, tol=5e-4)
close("Fisher odds ratio", float(odds), 0.8718, tol=5e-4)
check("all three tests agree on the verdict", p_z > 0.05 and p_chi > 0.05 and p_fisher > 0.05)

section("6. Power accounting")


def power_two_prop(p1, p2, n, alpha=0.05):
    se = np.sqrt(p1 * (1 - p1) / n + p2 * (1 - p2) / n)
    return stats.norm.cdf(abs(p2 - p1) / se - stats.norm.ppf(1 - alpha / 2))


def required_n_per_arm(p1, p2, power=0.80, alpha=0.05):
    k = (stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)) ** 2
    return int(np.ceil(k * (p1 * (1 - p1) + p2 * (1 - p2)) / (p2 - p1) ** 2))


check("power for the observed effect at n=200/arm is the published 10.1%",
      round(float(power_two_prop(p_a, p_b, 200)) * 100, 1) == 10.1,
      f"{power_two_prop(p_a, p_b, 200) * 100:.4f}%")
mde_pp = float((stats.norm.ppf(0.975) + stats.norm.ppf(0.80)) * se_diff * 100)
check("minimum detectable effect is the published 14.0 pp", round(mde_pp, 1) == 14.0,
      f"{mde_pp:.4f}")
check("MDE is ~4x the observed effect", 3.5 < mde_pp / diff_pp < 4.5,
      f"{mde_pp / diff_pp:.2f}x")
check("required n for +3.43 pp", required_n_per_arm(p_a, p_b) == 3335,
      str(required_n_per_arm(p_a, p_b)))
check("required n for +5 pp", required_n_per_arm(p_a, p_a + 0.05) == 1566,
      str(required_n_per_arm(p_a, p_a + 0.05)))
check("required n for +10 pp", required_n_per_arm(p_a, p_a + 0.10) == 388,
      str(required_n_per_arm(p_a, p_a + 0.10)))
check("underpowered: power below 0.20", power_two_prop(p_a, p_b, 200) < 0.20)
check("power rises with n",
      power_two_prop(p_a, p_a + 0.05, 200) < power_two_prop(p_a, p_a + 0.05, 1566)
      < power_two_prop(p_a, p_a + 0.05, 3000))

section("7. Peeking diagnostic")
daily = users.groupby(["assignment_date", "variant"]).agg(
    users_n=("user_id", "count"), conversions=("converted", "sum")).reset_index()
piv = daily.pivot(index="assignment_date", columns="variant").fillna(0)
piv.columns = ["u_A", "u_B", "c_A", "c_B"]
piv["n_A"] = piv["u_A"].cumsum()
piv["n_B"] = piv["u_B"].cumsum()
piv["conv_A"] = piv["c_A"].cumsum()
piv["conv_B"] = piv["c_B"].cumsum()
piv["rate_A"] = piv["conv_A"] / piv["n_A"]
piv["rate_B"] = piv["conv_B"] / piv["n_B"]
piv["diff_pp"] = (piv["rate_B"] - piv["rate_A"]) * 100
pool = (piv["conv_A"] + piv["conv_B"]) / (piv["n_A"] + piv["n_B"])
se_c = np.sqrt(pool * (1 - pool) * (1 / piv["n_A"] + 1 / piv["n_B"]))
piv["p_value"] = 2 * stats.norm.sf(np.abs(piv["diff_pp"] / 100 / se_c))
check("14 cumulative readouts", len(piv) == 14, f"{len(piv)}")
close("cumulative readout ends at the primary p-value",
      float(piv["p_value"].iloc[-1]), p_z, tol=1e-4)
close("cumulative readout ends at the primary difference",
      float(piv["diff_pp"].iloc[-1]), diff_pp, tol=1e-3)
close("lowest cumulative p-value over the run", float(piv["p_value"].min()), 0.2984, tol=1e-3)
check("never crosses 0.05 on any day", bool((piv["p_value"] > 0.05).all()))
check("the low point is day 1, on the smallest sample",
      piv["p_value"].idxmin() == piv.index[0] and int(piv["n_A"].iloc[0]) == 20
      and int(piv["n_B"].iloc[0]) == 9, str(piv["p_value"].idxmin()))
check("reported bound: min cumulative p rounds to 0.298",
      0.29 <= float(piv["p_value"].min()) < 0.30, f"{piv['p_value'].min():.4f}")
last3 = piv["p_value"].tail(3).round(2)
check("last three readouts round to within 0.42 - 0.49",
      bool(((last3 >= 0.42) & (last3 <= 0.49)).all()),
      " ".join(f"{p:.2f}" for p in last3))
close("mean users per arm per day (Control_A)", n_a / 14, 14.6429, tol=1e-4)
close("mean users per arm per day (Variant_B)", n_b / 14, 13.9286, tol=1e-4)
check("prose bound: ~14 users per arm per day", 13.9 <= n_b / 14 and n_a / 14 <= 14.7)
check("final cumulative users reconcile", int(piv["n_A"].iloc[-1]) == 205
      and int(piv["n_B"].iloc[-1]) == 195)

section("8. Secondary metrics and guardrail")
funnel = df.groupby(["variant", "event_name"])["session_id"].nunique().unstack(fill_value=0)
check("funnel Control_A 205/205/96/109",
      funnel.loc["Control_A", ["page_view", "checkout_start", "checkout_complete", "drop_off"]].tolist()
      == [205, 205, 96, 109])
check("funnel Variant_B 195/195/98/97",
      funnel.loc["Variant_B", ["page_view", "checkout_start", "checkout_complete", "drop_off"]].tolist()
      == [195, 195, 98, 97])
check("both arms reach checkout_start at 100%",
      (funnel["checkout_start"] == funnel["page_view"]).all())
close("Control_A drop-off %", 100 * 109 / 205, 53.1707, tol=5e-4)
close("Variant_B drop-off %", 100 * 97 / 195, 49.7436, tol=5e-4)
dur = users.groupby("variant")["session_duration_sec"].agg(["mean", "median", "std"])
check("mean duration Control_A is the published 40.0s",
      round(float(dur.loc["Control_A", "mean"]), 1) == 40.0,
      f"{dur.loc['Control_A', 'mean']:.4f}")
check("mean duration Variant_B is the published 40.5s",
      round(float(dur.loc["Variant_B", "mean"]), 1) == 40.5,
      f"{dur.loc['Variant_B', 'mean']:.4f}")
close("median duration Control_A", float(dur.loc["Control_A", "median"]), 39.0)
close("median duration Variant_B", float(dur.loc["Variant_B", "median"]), 40.0)
t_stat, p_welch = stats.ttest_ind(B["session_duration_sec"], A["session_duration_sec"],
                                  equal_var=False)
check("Welch t is the published 0.507", round(float(t_stat), 3) == 0.507, f"{t_stat:.6f}")
check("Welch p is the published 0.612", round(float(p_welch), 3) == 0.612, f"{p_welch:.6f}")
check("duration guardrail is neutral", p_welch > 0.05
      and abs(float(dur.loc["Variant_B", "mean"] - dur.loc["Control_A", "mean"])) < 1.0)
by_out = users.groupby(["variant", "converted"])["session_duration_sec"].mean().unstack()
for v, dropped, completed in [("Control_A", 36.6, 43.8), ("Variant_B", 35.5, 45.4)]:
    check(f"{v} dropped-off duration is the published {dropped}s",
          round(float(by_out.loc[v, 0]), 1) == dropped, f"{by_out.loc[v, 0]:.4f}")
    check(f"{v} completed duration is the published {completed}s",
          round(float(by_out.loc[v, 1]), 1) == completed, f"{by_out.loc[v, 1]:.4f}")
check("completed sessions outlast dropped ones in both arms",
      bool((by_out[1] > by_out[0]).all()))

pool_dev = users.groupby("device_type")["converted"].agg(["size", "sum"])
pool_dev["drop_pct"] = 100 * (pool_dev["size"] - pool_dev["sum"]) / pool_dev["size"]
close("Mobile drop-off %, both arms pooled", float(pool_dev.loc["Mobile", "drop_pct"]),
      67.4847, tol=5e-4)
close("Desktop drop-off %, both arms pooled", float(pool_dev.loc["Desktop", "drop_pct"]),
      41.7989, tol=5e-4)
close("Tablet drop-off %, both arms pooled", float(pool_dev.loc["Tablet", "drop_pct"]),
      35.4167, tol=5e-4)
check("Mobile really is the highest-drop-off cohort",
      pool_dev["drop_pct"].idxmax() == "Mobile", pool_dev["drop_pct"].idxmax())
check("Mobile has 163 users", int(pool_dev.loc["Mobile", "size"]) == 163,
      f"{int(pool_dev.loc['Mobile', 'size'])}")


def subgroup_table(frame, col):
    rows = []
    for name, g in frame.groupby(col):
        a = g[g["variant"] == "Control_A"]
        b = g[g["variant"] == "Variant_B"]
        pa, pb = a["converted"].mean(), b["converted"].mean()
        se = np.sqrt(pa * (1 - pa) / len(a) + pb * (1 - pb) / len(b))
        d = (pb - pa) * 100
        rows.append({col: name, "n_A": len(a), "rate_A_pct": round(pa * 100, 2),
                     "n_B": len(b), "rate_B_pct": round(pb * 100, 2),
                     "diff_pp": round(d, 2),
                     "ci_low": round(d - 1.96 * se * 100, 2),
                     "ci_high": round(d + 1.96 * se * 100, 2),
                     "p_value": round(float(2 * stats.norm.sf(abs((pb - pa) / se))), 4)})
    return pd.DataFrame(rows)


section("9. Heterogeneity: device, segment, interaction")
device_table = subgroup_table(users, "device_type")
want_device = {
    "Desktop": dict(n_A=98, rate_A_pct=51.02, n_B=91, rate_B_pct=65.93, diff_pp=14.91,
                    ci_low=1.03, ci_high=28.80, p_value=0.0353),
    "Mobile": dict(n_A=82, rate_A_pct=37.80, n_B=81, rate_B_pct=27.16, diff_pp=-10.64,
                   ci_low=-24.93, ci_high=3.64, p_value=0.1441),
    "Tablet": dict(n_A=25, rate_A_pct=60.00, n_B=23, rate_B_pct=69.57, diff_pp=9.57,
                   ci_low=-17.31, ci_high=36.44, p_value=0.4855),
}
for name, want in want_device.items():
    got = device_table[device_table["device_type"] == name].iloc[0]
    check(f"device row {name} matches notebook table",
          all(got[k] == v for k, v in want.items()),
          " ".join(f"{k}={got[k]}" for k in want))
check("device subgroup counts sum to 400",
      int(device_table["n_A"].sum() + device_table["n_B"].sum()) == 400)
check("device effects really are opposite signs",
      device_table.set_index("device_type")["diff_pp"]["Desktop"] > 0
      and device_table.set_index("device_type")["diff_pp"]["Mobile"] < 0)
check("Tablet is the smallest cell (n = 48 combined)",
      int(device_table.set_index("device_type").loc["Tablet", ["n_A", "n_B"]].sum()) == 48)
bonf = 0.05 / 3
close("Bonferroni threshold across 3 device tests", bonf, 0.0166667, tol=1e-5)
check("Desktop p = 0.035 does NOT survive Bonferroni",
      device_table.set_index("device_type").loc["Desktop", "p_value"] > bonf)
check("no device row survives Bonferroni on its own",
      bool((device_table["p_value"] > bonf).all()))

segment_table = subgroup_table(users, "user_segment")
want_segment = {
    "Enterprise": dict(n_A=29, rate_A_pct=51.72, n_B=40, rate_B_pct=55.00, diff_pp=3.28,
                       ci_low=-20.57, ci_high=27.12, p_value=0.7877),
    "Free": dict(n_A=114, rate_A_pct=44.74, n_B=105, rate_B_pct=49.52, diff_pp=4.79,
                 ci_low=-8.43, ci_high=18.01, p_value=0.4779),
    "Premium": dict(n_A=62, rate_A_pct=48.39, n_B=50, rate_B_pct=48.00, diff_pp=-0.39,
                    ci_low=-19.00, ci_high=18.23, p_value=0.9675),
}
for name, want in want_segment.items():
    got = segment_table[segment_table["user_segment"] == name].iloc[0]
    check(f"segment row {name} matches notebook table",
          all(got[k] == v for k, v in want.items()),
          " ".join(f"{k}={got[k]}" for k in want))
check("reported bound: every segment p >= 0.47", bool((segment_table["p_value"] >= 0.47).all()),
      f"min = {segment_table['p_value'].min():.4f}")


def logit_fit(X, y):
    """Newton-Raphson IRLS logistic fit; returns coefs, SEs and Wald p-values."""
    b = np.zeros(X.shape[1])
    for _ in range(200):
        p = 1 / (1 + np.exp(-X @ b))
        W = p * (1 - p)
        H = X.T @ (X * W[:, None])
        step = np.linalg.solve(H, X.T @ (y - p))
        b = b + step
        if np.max(np.abs(step)) < 1e-11:
            break
    p = 1 / (1 + np.exp(-X @ b))
    se = np.sqrt(np.diag(np.linalg.inv(X.T @ (X * (p * (1 - p))[:, None]))))
    return b, se, 2 * stats.norm.sf(np.abs(b / se))


def interaction_terms(frame, col):
    ref = sorted(frame[col].unique())[0]
    m = frame.assign(variant_bin=(frame["variant"] == "Variant_B").astype(int))
    names = ["Intercept", "variant_bin"]
    cols = [np.ones(len(m)), m["variant_bin"].values.astype(float)]
    for level in sorted(frame[col].unique())[1:]:
        d = (m[col] == level).astype(float).values
        cols += [d, d * m["variant_bin"].values.astype(float)]
        names += [f"{col}[T.{level}]", f"variant_bin:{col}[T.{level}]"]
    X = np.column_stack(cols)
    c, se, pv = logit_fit(X, m["converted"].values.astype(float))
    return pd.DataFrame({"term": names, "coef": np.round(c, 3),
                         "se": np.round(se, 3), "p_value": np.round(pv, 3)},
                        index=range(len(names))).set_index("term")


dev_fit = interaction_terms(users, "device_type")
want_interaction = {  # the statsmodels output stored in the notebook
    "Intercept": (0.041, 0.840),
    "variant_bin": (0.620, 0.039),
    "device_type[T.Mobile]": (-0.539, 0.077),
    "device_type[T.Tablet]": (0.365, 0.423),
    "variant_bin:device_type[T.Mobile]": (-1.108, 0.014),
    "variant_bin:device_type[T.Tablet]": (-0.198, 0.770),
}
for term, (coef, pv) in want_interaction.items():
    close(f"device interaction {term} coef", float(dev_fit.loc[term, "coef"]), coef, tol=1e-3)
    close(f"device interaction {term} p", float(dev_fit.loc[term, "p_value"]), pv, tol=1e-3)
check("variant x Mobile interaction is significant",
      float(dev_fit.loc["variant_bin:device_type[T.Mobile]", "p_value"]) < 0.05)
check("reference level is Desktop (alphabetical), as the notebook asserts",
      sorted(users["device_type"].unique())[0] == "Desktop")

seg_fit = interaction_terms(users, "user_segment")
seg_int = seg_fit.loc[[t for t in seg_fit.index if t.startswith("variant_bin:")]]
check("no segment interaction is significant", bool((seg_int["p_value"] > 0.05).all()),
      " ".join(f"{t}={p}" for t, p in seg_int["p_value"].items()))
check("reported bound: segment interaction p >= 0.81",
      bool((seg_int["p_value"] >= 0.81).all()), f"min = {seg_int['p_value'].min():.3f}")

section("10. Bayesian cross-check (seed 42, as in the notebook)")
rng = np.random.default_rng(42)
post_a = rng.beta(1 + A["converted"].sum(), 1 + len(A) - A["converted"].sum(), 400_000)
post_b = rng.beta(1 + B["converted"].sum(), 1 + len(B) - B["converted"].sum(), 400_000)
p_b_better = float((post_b > post_a).mean())
loss_ship_b = float(np.maximum(post_a - post_b, 0).mean() * 100)
loss_ship_a = float(np.maximum(post_b - post_a, 0).mean() * 100)
close("P(Variant_B > Control_A)", p_b_better, 0.7525, tol=2e-3)
check("P(B > A) is the published 75.3%", round(p_b_better * 100, 1) == 75.3,
      f"{p_b_better * 100:.4f}%")
check("expected loss of shipping B is the published 0.73 pp", round(loss_ship_b, 2) == 0.73,
      f"{loss_ship_b:.4f}")
check("expected loss of keeping A is the published 4.12 pp", round(loss_ship_a, 2) == 4.12,
      f"{loss_ship_a:.4f}")
check("Bayesian read agrees: B is not confidently better", p_b_better < 0.95)
check("posterior mean difference matches the frequentist point estimate direction",
      float(post_b.mean() - post_a.mean()) > 0)

section("11. Notebook stored outputs vs recomputation")
nb = json.loads((BASE / "python" / "ab_test_significance.ipynb").read_text(encoding="utf-8"))
out_text = {}
for cell in nb["cells"]:
    buf = []
    for o in cell.get("outputs", []):
        if o.get("output_type") == "stream":
            buf.append("".join(o.get("text", [])))
        elif "data" in o:
            for key in ("text/plain", "text/html"):
                if key in o["data"]:
                    buf.append("".join(o["data"][key]))
    out_text[cell["id"]] = "".join(buf)


def stored(cell_id, *needles, label=""):
    blob = out_text.get(cell_id, "")
    missing = [n for n in needles if n not in blob]
    check(f"notebook cell {cell_id} output: {label or needles[0]}", not missing,
          f"missing {missing}" if missing else "")


check("notebook has 72 cells", len(nb["cells"]) == 72, f"{len(nb['cells'])}")
stored("62454c50", "USR_2172", "checkout_start", label="load cell head(9) intact")
stored("e8eed045", "events: 1200", "users: 400", "sessions: 400", label="shape prints")
stored("665103cc", "user_id           0", label="no nulls")
stored("2465f355", "duplicate rows: 0", "3    400", label="dupes + events/session")
stored("d72c80ef", "users in both variants: 0", "sessions in both variants: 0",
       "sessions per user: {1: 400}", label="contamination")
stored("9ccd1343", "device drift in session: 0", "segment drift in session: 0", label="drift")
stored("9f5afe3f", "checkout_complete rows with is_converted = 0: 0", label="flag reconciles")
stored("74d6888c", "Control_A    205", "Variant_B    195", label="split")
stored("a99fc543", f"SRM chi-square = {chi2_srm:.3f}", f"p = {p_srm:.4f}", label="SRM print")
stored("e8ff584e", f"chi-square = {float(chi2_dev):.3f}", f"p = {float(p_dev):.3f}",
       label="device balance")
stored("fc26d1ac", f"chi-square = {float(chi2_seg):.3f}", f"p = {float(p_seg):.3f}",
       label="segment balance")
stored("eee9297c", "205           96              46.8293", "195           98              50.2564",
       label="scorecard table")
stored("1654a733", f"Control_A rate: {p_a * 100:.2f}%", f"Variant_B rate: {p_b * 100:.2f}%",
       f"absolute difference: {diff_pp:+.2f} percentage points",
       f"relative lift: {rel_lift:+.2f}%", label="primary prints")
stored("4225cdf4", f"95% CI for the difference: {ci_low:+.2f} to {ci_high:+.2f} pp",
       label="CI print")
stored("b3b16423", f"z = {z_stat:.3f}, two-sided p = {p_z:.4f}", label="z print")
stored("50f8cbc2", f"chi-square = {float(chi2_prim):.4f}, p = {float(p_chi):.4f}",
       label="chi print")
stored("e6827f21", f"Fisher exact p = {float(p_fisher):.4f} (odds ratio {float(odds):.3f})",
       label="fisher print")
stored("2885ef55", f"{power_two_prop(p_a, p_b, 200) * 100:.1f}%", f"{mde_pp:.1f} pp",
       label="power + MDE prints")
stored("ba63e6b0", "3335", "1566", "388", label="required n table")
stored("324264a5", "0.4640", "0.5060", "0.4930", "4.1830", label="cumulative daily table")
stored("4df8a370", "Control_A         205             205                 96       109",
       "Variant_B         195             195                 98        97", label="funnel")
stored("4c7ed517", "46.8300   53.1700", "50.2600   49.7400", label="funnel pct")
stored("57cbc0e9", "205 40.0000 39.0000", "195 40.5000 40.0000", label="duration stats")
stored("6ed4da08", f"Welch t = {float(t_stat):.3f}, p = {float(p_welch):.3f}",
       f"difference {float(dur.loc['Variant_B', 'mean']) - float(dur.loc['Control_A', 'mean']):+.1f}s",
       label="welch print")
stored("9cd1b78d", "Control_A 36.6000 43.8000", "Variant_B 35.5000 45.4000",
       label="duration by outcome")
stored("8ba956b1", "51.0200", "65.9300", "14.9100", "-10.6400", "9.5700", "0.0353", "0.1441",
       "0.4855", label="device table")
stored("5b7a92fe", "51.7200", "55.0000", "44.7400", "49.5200", "4.7900", "48.3900", "48.0000",
       "-0.3900", "0.7877", "0.4779", "0.9675", label="segment table")
stored("c95057ca", "0.0410   0.8400", "-0.5390   0.0770", "0.3650   0.4230", "0.6200   0.0390",
       "-1.1080   0.0140", "-0.1980   0.7700", label="statsmodels interaction output")
stored("28c5d490", f"= {p_b_better * 100:.1f}%",
       f"ship B and B is actually worse: {loss_ship_b:.2f} pp",
       f"keep A and B is actually better: {loss_ship_a:.2f} pp", label="bayesian prints")

markdown_text = "\n\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown")
code_text = "\n\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
check("every code cell in the notebook has been executed",
      not [c["id"] for c in nb["cells"]
           if c["cell_type"] == "code" and not c.get("execution_count")],
      str([c["id"] for c in nb["cells"]
           if c["cell_type"] == "code" and not c.get("execution_count")]))
check("the statsmodels cell keeps its try/except guard",
      "statsmodels not available" in code_text)

section("12. Published prose vs recomputation")


def prose(path, text):
    content = (BASE / path).read_text(encoding="utf-8")
    check(f"{path} states {text!r}", text in content)


R = "README.md"
D = "data/experiment_design.md"
prose(R, "+3.43 pp"); prose(R, "50.26% vs 46.83%"); prose(R, "p = 0.493")
prose(R, "6.37 to +13.22 pp"); prose(R, "z = 0.686"); prose(R, "Fisher p = 0.548")
prose(R, "1,200 events"); prose(R, "400 users"); prose(R, "205 (51.25%)"); prose(R, "195 (48.75%)")
prose(R, "0.250, p = 0.617"); prose(R, "device p = 0.952, segment p = 0.206")
prose(R, "51.02% (98)"); prose(R, "65.93% (91)"); prose(R, "+14.91 pp")
prose(R, "60.00% (25)"); prose(R, "69.57% (23)"); prose(R, "+9.57 pp")
prose(R, "37.80% (82)"); prose(R, "27.16% (81)"); prose(R, "10.64 pp")
prose(R, "threshold 0.0167"); prose(R, "p = 0.014")
prose(R, "40.0s (A) vs 40.5s (B), Welch p = 0.612")
prose(R, "Users/arm needed for +3.43 pp"); prose(R, "~3,335"); prose(R, "~1,566")
prose(D, "+3.43 pp (p = 0.493"); prose(D, "1,566 users per arm"); prose(D, "3,335 per arm")
prose(D, "interaction signal (p = 0.014)"); prose(D, "at only ~10% power")
prose(D, "Control_A 205 users (51.25%) / Variant_B 195 users (48.75%)")
prose(D, "400 users, 400 sessions, 1,200 events (exactly 3 events per session)")
prose(D, "10–23 March 2026 (14 complete days)")
prose(D, "Mobile point estimate −10.6 pp")
check("design doc total sample for the +5 pp re-run", 2 * 1566 == 3132)
check("Mobile point estimate rounds to the published -10.6 pp",
      round(float(device_table.set_index("device_type").loc["Mobile", "diff_pp"]), 1) == -10.6)
prose("sql/01_cohort_assignment.sql", "chi2 = 0.250 on this data, p = 0.617")
prose("sql/02_metric_aggregation.sql", "Control_A 205 users, 96 conversions, 46.83%")
prose("sql/02_metric_aggregation.sql", "Variant_B 195 users, 98 conversions, 50.26%")
prose(D, "67.5% of its 163 users"); prose(D, "41.8% Desktop, 35.4% Tablet")
prose(D, "python verify_analysis.py")
prose(R, "USR_2001"); prose(R, "SES_8172"); prose(R, "synthetic")
prose(R, "verify_analysis.py"); prose(R, "requirements.txt")
check("design doc drop-off figures match recomputation",
      f"{pool_dev.loc['Mobile', 'drop_pct']:.1f}%" == "67.5%"
      and f"{pool_dev.loc['Desktop', 'drop_pct']:.1f}%" == "41.8%"
      and f"{pool_dev.loc['Tablet', 'drop_pct']:.1f}%" == "35.4%")
check("notebook markdown cites the repo-relative data path",
      "data/raw_experiment_logs.csv" in markdown_text)
check("notebook markdown has no stale ../data reference", "../data/" not in markdown_text)
check("notebook markdown states the corrected segment rounding", "+4.79 pp" in markdown_text)
check("notebook markdown states the corrected balance bound", "p ≥ 0.20" in markdown_text)
check("notebook markdown states the corrected daily sample size",
      "~14 users per arm per day" in markdown_text)
check("notebook markdown no longer claims a 0.42 floor", "never drops below" not in markdown_text)
check("README no longer claims a 0.42 floor", "never below" not in (BASE / R).read_text(encoding="utf-8"))
check("README no longer claims segment p >= 0.48", "p ≥ 0.48" not in (BASE / R).read_text(encoding="utf-8"))
check("notebook markdown uses the corrected peeking wording",
      "never approaches 0.05" in markdown_text)
check("day-1 rates are the 35% vs 56% quoted in the peeking note",
      round(float(piv["rate_A"].iloc[0]) * 100) == 35
      and round(float(piv["rate_B"].iloc[0]) * 100) == 56,
      f"{piv['rate_A'].iloc[0]:.4f} / {piv['rate_B'].iloc[0]:.4f}")

bound_claims = [
    ("README: segment p bound", float(segment_table["p_value"].min()), 0.47),
    ("README: segment interaction bound", float(seg_int["p_value"].min()), 0.81),
    ("README: minimum cumulative p", float(piv["p_value"].min()), 0.29),
    ("notebook: minimum balance p", float(min(p_dev, p_seg)), 0.20),
]
for label, got, floor in bound_claims:
    check(f"{label} ({got:.3f} >= {floor})", got >= floor, f"{got:.4f}")

check("README publishes no stale segment rounding", "+4.78" not in (BASE / R).read_text(encoding="utf-8"))
check("notebook publishes no stale segment rounding", "+4.78 pp" not in markdown_text)
check("notebook uses the rounding-safe balance bound", "p >= 0.21" not in markdown_text)

section("13. Repo completeness")
required = ["README.md", "LICENSE", "requirements.txt", ".gitignore", ".gitattributes",
            "data/raw_experiment_logs.csv", "data/experiment_design.md",
            "sql/01_cohort_assignment.sql", "sql/02_metric_aggregation.sql",
            "python/ab_test_significance.ipynb", "python/exploratory_visuals.py"]
for rel in required:
    check(f"{rel} exists", (BASE / rel).exists())
charts = sorted((BASE / "python" / "charts").glob("*.png"))
check("chart pack has 6 PNGs", len(charts) == 6, f"{len(charts)}")
check("every chart PNG is a real PNG over 10 KB",
      all(c.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and c.stat().st_size > 10_000 for c in charts))
check("no CSV credentials or tokens in the logs",
      not re.search(r"(?i)(api[_-]?key|secret|password|bearer|ghp_[A-Za-z0-9]{20,})", LOGS.read_text()))
check("README file tree lists every tracked file",
      all(name in (BASE / R).read_text(encoding="utf-8")
          for name in [p.name for p in [LOGS, BASE / "data" / "experiment_design.md",
                                        BASE / "sql" / "01_cohort_assignment.sql",
                                        BASE / "sql" / "02_metric_aggregation.sql",
                                        BASE / "python" / "ab_test_significance.ipynb",
                                        BASE / "python" / "exploratory_visuals.py"]]))

n_total = passed + failed + 1  # +1: this check counts itself
readme = (BASE / R).read_text(encoding="utf-8")
m = re.search(r"It runs (\d+) checks in (\d+) sections", readme)
check(f"README's own counts are accurate ({n_total} checks, 13 sections)",
      bool(m) and int(m.group(1)) == n_total and int(m.group(2)) == 13,
      m.group(0) if m else "no counts stated")

print(f"\n{n_total} checks run, " + (f"{failed} FAILED" if failed else "ALL PASS"))
raise SystemExit(1 if failed else 0)
