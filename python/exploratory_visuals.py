"""
exploratory_visuals.py
Digital Product AB Testing Analytics

Generates the ad-hoc chart pack for presentations from
data/raw_experiment_logs.csv. Every chart is computed at USER level
(the randomization unit), matching ab_test_significance.ipynb.

Run:  python python/exploratory_visuals.py
Out:  python/charts/*.png  (6 charts, 150 dpi)
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

BASE = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "charts"
OUT.mkdir(exist_ok=True)

BLUE, RED, GREEN, GREY = "#4C72B0", "#C44E52", "#55A868", "#8C8C8C"

df = pd.read_csv(BASE / "data" / "raw_experiment_logs.csv", parse_dates=["timestamp"])
users = df.groupby("user_id").agg(
    variant=("variant", "first"), device=("device_type", "first"),
    segment=("user_segment", "first"), converted=("is_converted", "max"),
    duration=("time_spent_sec", "max"),
    date=("timestamp", lambda s: s.min().date())).reset_index()
A = users[users["variant"] == "Control_A"]
B = users[users["variant"] == "Variant_B"]


def rate_ci(g):
    n, k = len(g), g["converted"].sum()
    p = k / n
    se = np.sqrt(p * (1 - p) / n)
    return p * 100, 1.96 * se * 100


# ---------------------------------------------------------------- 1. rates
fig, ax = plt.subplots(figsize=(7, 4.2))
labels, vals, errs = [], [], []
for name, g in [("Control_A", A), ("Variant_B", B)]:
    p, e = rate_ci(g)
    labels.append(f"{name}\n(n={len(g)})"); vals.append(p); errs.append(e)
ax.bar(labels, vals, yerr=errs, capsize=6, color=[GREY, BLUE], alpha=0.9)
for i, (v, e) in enumerate(zip(vals, errs)):
    ax.text(i, v + e + 1.5, f"{v:.2f}%", ha="center", fontweight="bold")
ax.set_ylabel("checkout completion rate %")
ax.set_title("Conversion by variant — +3.43 pp, 95% CI -6.37 to +13.22 (p = 0.49)")
ax.set_ylim(0, 65)
fig.tight_layout(); fig.savefig(OUT / "01_conversion_by_variant.png", dpi=150); plt.close(fig)

# ---------------------------------------------------------------- 2. funnel
stages = ["page_view", "checkout_start", "checkout_complete"]
funnel = {v: [df[(df["variant"] == v) & (df["event_name"] == e)]["session_id"].nunique()
              for e in stages] for v in ["Control_A", "Variant_B"]}
fig, ax = plt.subplots(figsize=(8, 4.2))
y = np.arange(len(stages))
ax.barh(y - 0.2, funnel["Control_A"], height=0.4, label="Control_A", color=GREY)
ax.barh(y + 0.2, funnel["Variant_B"], height=0.4, label="Variant_B", color=BLUE)
ax.set_yticks(y, ["Page view\n/checkout", "Checkout start\n/checkout-step1",
                  "Checkout complete\n/checkout-confirmation"])
ax.invert_yaxis(); ax.set_xlabel("unique sessions")
ax.set_title("Checkout funnel by variant (drop_off is the non-completing branch)")
ax.legend()
for bars in ax.containers:
    ax.bar_label(bars, padding=3)
fig.tight_layout(); fig.savefig(OUT / "02_funnel_by_variant.png", dpi=150); plt.close(fig)

# ---------------------------------------------------------------- 3. forest
rows = []
for d, g in users.groupby("device"):
    a, b = g[g["variant"] == "Control_A"], g[g["variant"] == "Variant_B"]
    pa, pb = a["converted"].mean(), b["converted"].mean()
    se = np.sqrt(pa * (1 - pa) / len(a) + pb * (1 - pb) / len(b))
    rows.append((d, (pb - pa) * 100, 1.96 * se * 100, len(a) + len(b)))
rows.sort(key=lambda r: r[1])
fig, ax = plt.subplots(figsize=(8, 3.8))
ax.errorbar([r[1] for r in rows], [r[0] for r in rows],
            xerr=[r[2] for r in rows], fmt="o", color=BLUE, capsize=5)
ax.axvline(0, color=RED, linestyle="--", linewidth=1)
ax.axvline(3.43, color=GREY, linestyle=":", linewidth=1, label="overall effect +3.43 pp")
for i, r in enumerate(rows):
    ax.text(r[1] + r[2] + 1.5, i, f"{r[1]:+.1f} pp (n={r[3]})", va="center")
ax.set_xlabel("Variant_B - Control_A conversion difference (pp, 95% CI)")
ax.set_title("Effect by device - the average hides opposite signs")
ax.legend(loc="lower left")
fig.tight_layout(); fig.savefig(OUT / "03_device_forest.png", dpi=150); plt.close(fig)

# ---------------------------------------------------------------- 4. daily
daily = users.groupby(["date", "variant"])["converted"].agg(["size", "mean"]).reset_index()
fig, ax = plt.subplots(figsize=(9, 4.2))
for v, g in daily.groupby("variant"):
    ax.plot(g["date"], g["mean"] * 100, marker="o", label=v,
            color=GREY if v == "Control_A" else BLUE)
ax.set_ylabel("daily conversion rate %")
ax.set_title("Daily conversion rate by variant (noisy at ~15 users per arm per day)")
ax.legend(); plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
fig.tight_layout(); fig.savefig(OUT / "04_daily_trend.png", dpi=150); plt.close(fig)

# ---------------------------------------------------------------- 5. duration
groups, glabels = [], []
for v, g in [("Control_A", A), ("Variant_B", B)]:
    groups += [g.loc[g["converted"] == 0, "duration"], g.loc[g["converted"] == 1, "duration"]]
    glabels += [f"{v}\ndropped off", f"{v}\ncompleted"]
fig, ax = plt.subplots(figsize=(8, 4.2))
ax.boxplot(groups, labels=glabels, patch_artist=True,
           boxprops=dict(facecolor="#DCE6F1"))
ax.set_ylabel("session duration (sec, elapsed at last event)")
ax.set_title("Session duration by variant and outcome - guardrail is neutral")
fig.tight_layout(); fig.savefig(OUT / "05_duration_box.png", dpi=150); plt.close(fig)

# ---------------------------------------------------------------- 6. power
def power_two_prop(p1, p2, n, alpha=0.05):
    se = np.sqrt(p1 * (1 - p1) / n + p2 * (1 - p2) / n)
    return stats.norm.cdf(abs(p2 - p1) / se - stats.norm.ppf(1 - alpha / 2))

ns = np.arange(50, 3600, 25)
pa = A["converted"].mean()
fig, ax = plt.subplots(figsize=(8, 4.2))
ax.plot(ns, [power_two_prop(pa, pa + 0.0343, n) for n in ns], label="observed effect +3.43 pp", color=BLUE)
ax.plot(ns, [power_two_prop(pa, pa + 0.05, n) for n in ns], label="target effect +5 pp", color=GREEN)
ax.axhline(0.8, color=RED, linestyle="--", linewidth=1, label="80% power")
ax.axvline(200, color=GREY, linestyle=":", linewidth=1, label="this run (~200/arm)")
ax.set_xlabel("users per arm"); ax.set_ylabel("power")
ax.set_title("Power curve - this run could only detect effects of ~14 pp or larger")
ax.legend()
fig.tight_layout(); fig.savefig(OUT / "06_power_curve.png", dpi=150); plt.close(fig)

print("charts written to", OUT)
for p in sorted(OUT.glob("*.png")):
    print(" -", p.name, f"{p.stat().st_size / 1024:.0f} KB")
