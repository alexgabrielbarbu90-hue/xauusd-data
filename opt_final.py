#!/usr/bin/env python3
"""Compara configul VERDE original vs configul OPTIMIZAT (selectat pe train)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import fvg_lvn_v2 as v2
import fvg_lvn_backtest as bt

START, RISK = 100_000.0, 0.01
df = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")
dates = sorted(df["date"].unique())
SPLIT = dates[int(len(dates) * 0.6)]

CONFIGS = {
    "Original (10-23, RR1)":
        v2.defaults(rr=1.0, hours=True, h0=10, h1=23, bin=2, lvn_pct=20,
                    min_gap=1.5, sl_buf=0.5),
    "Optimizat (15-19, RR1.5)":
        v2.defaults(rr=1.5, hours=True, h0=15, h1=19, bin=2, lvn_pct=25,
                    min_gap=1.5, sl_buf=0.25),
}


def equity(t):
    t = t.sort_values("entry_dt").reset_index(drop=True)
    eq, rows = START, []
    for _, r in t.iterrows():
        eq += r["R"] * RISK * eq
        rows.append({"dt": r["entry_dt"], "equity": eq, "R": r["R"], "date": r["date"]})
    return pd.DataFrame(rows)


def met(e):
    if e.empty:
        return dict(n=0, wr=0, final=START, ret=0, maxdd=0, sharpe=0, pf=0)
    peak = e["equity"].cummax()
    dd = ((e["equity"] - peak) / peak).min() * 100
    R = e["R"]; gp = R[R > 0].sum(); gl = -R[R < 0].sum()
    return dict(n=len(e), wr=(R > 0).mean() * 100, final=e["equity"].iloc[-1],
                ret=(e["equity"].iloc[-1] / START - 1) * 100, maxdd=dd,
                sharpe=R.mean() / R.std() * np.sqrt(len(R)) if R.std() > 0 else 0,
                pf=gp / gl if gl > 0 else 99)


curves = {n: equity(v2.run(df, p)) for n, p in CONFIGS.items()}

print("=" * 100)
print(f"{'Config':28s} {'Trades':>7s} {'WR':>6s} {'Final $':>12s} {'Return':>9s} "
      f"{'MaxDD':>8s} {'Sharpe':>7s} {'PF':>6s}")
print("-" * 100)
for n, e in curves.items():
    m = met(e)
    print(f"{n:28s} {m['n']:7d} {m['wr']:5.1f}% {m['final']:>12,.0f} {m['ret']:>+8.1f}% "
          f"{m['maxdd']:>7.1f}% {m['sharpe']:>7.2f} {m['pf']:>6.2f}")
print("=" * 100)
print("\nDefalcare TRAIN / TEST (out-of-sample) pt. configul optimizat:")
e = curves["Optimizat (15-19, RR1.5)"]
for nm, sub in [("TRAIN", e[e.date < SPLIT]), ("TEST (OOS)", e[e.date >= SPLIT])]:
    # re-baze equity pe sub-perioada
    R = sub["R"].values; eq = START; eqs = []
    for r in R:
        eq += r * RISK * eq; eqs.append(eq)
    eqs = np.array(eqs); peak = np.maximum.accumulate(eqs) if len(eqs) else eqs
    dd = (((eqs - peak) / peak).min() * 100) if len(eqs) else 0
    ret = (eqs[-1] / START - 1) * 100 if len(eqs) else 0
    sh = R.mean() / R.std() * np.sqrt(len(R)) if len(R) and R.std() > 0 else 0
    print(f"  {nm:12s} n={len(R):3d}  WR={(R>0).mean()*100:5.1f}%  ret={ret:+7.1f}%  "
          f"maxDD={dd:6.1f}%  Sharpe={sh:.2f}")

# ---- plot
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), height_ratios=[2.4, 1], sharex=True)
cols = {"Original (10-23, RR1)": "#8c8c8c", "Optimizat (15-19, RR1.5)": "#2ca02c"}
for n, e in curves.items():
    ax1.plot(e["dt"], e["equity"], label=n, color=cols[n], lw=1.9)
ax1.axhline(START, color="black", ls="--", lw=0.8, alpha=0.5)
sd = pd.Timestamp(SPLIT)
for ax in (ax1, ax2):
    ax.axvline(sd, color="red", ls=":", lw=1.3, alpha=0.6)
ax1.text(sd, ax1.get_ylim()[1], "  split OOS (optimizare doar pe stanga)",
         color="red", va="top", fontsize=9)
ax1.set_ylabel("Equity ($)")
ax1.set_title("FVG + LVN 5M  —  Original vs Optimizat  |  $100k, risc 1%/trade",
              fontsize=13, fontweight="bold")
ax1.legend(loc="upper left", fontsize=10)
ax1.grid(alpha=0.25)
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))

e = curves["Optimizat (15-19, RR1.5)"]
peak = e["equity"].cummax(); dd = (e["equity"] - peak) / peak * 100
ax2.fill_between(e["dt"], dd, 0, color="#2ca02c", alpha=0.35)
ax2.set_ylabel("Drawdown %\n(optimizat)")
ax2.set_xlabel("Data")
ax2.grid(alpha=0.25)
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
fig.autofmt_xdate()
plt.tight_layout()
plt.savefig("equity_optimized.png", dpi=130, bbox_inches="tight")
print("\nGrafic -> equity_optimized.png")
