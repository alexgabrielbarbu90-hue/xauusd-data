#!/usr/bin/env python3
"""
Config VERDE (doar ore lichide) cu FVG detectate pe 15 MINUTE.
Resamplez 5M -> 15M, rulez acelasi pipeline, compar cu 5M si generez equity curve.
Capital $100k, risc 1% compus.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import fvg_lvn_v2 as v2
import fvg_lvn_backtest as bt

START, RISK = 100_000.0, 0.01


def resample_15m(df5):
    s = df5.set_index("dt")
    o = s["open"].resample("15min").first()
    h = s["high"].resample("15min").max()
    l = s["low"].resample("15min").min()
    c = s["close"].resample("15min").last()
    v = s["volume"].resample("15min").sum()
    r = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v})
    r = r.dropna(subset=["open", "high", "low", "close"])
    r = r[r["volume"] > 0]                      # scoate barele fara activitate
    r = r.reset_index()
    r["date"] = r["dt"].dt.date
    return r


def equity(trades):
    t = trades.sort_values("entry_dt").reset_index(drop=True)
    eq, rows = START, []
    for _, r in t.iterrows():
        eq += r["R"] * (RISK * eq)
        rows.append({"dt": r["entry_dt"], "equity": eq, "R": r["R"]})
    return pd.DataFrame(rows)


def metrics(e):
    if e.empty:
        return dict(n=0, wr=0, final=START, ret=0, maxdd=0, sharpe=0)
    peak = e["equity"].cummax()
    dd = ((e["equity"] - peak) / peak).min()
    r = e["R"]
    return dict(n=len(e), wr=(r > 0).mean() * 100, final=e["equity"].iloc[-1],
                ret=(e["equity"].iloc[-1] / START - 1) * 100, maxdd=dd * 100,
                sharpe=r.mean() / r.std() * np.sqrt(len(r)) if r.std() > 0 else 0)


df5 = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")
df15 = resample_15m(df5)
print(f"Bare 5M: {len(df5):,}  ->  Bare 15M: {len(df15):,}\n")

# config VERDE = doar ore lichide. max_hold scalat la 15M (96 bare = ~1 zi trading)
green_5m = v2.defaults(rr=1.0, hours=True)
green_15m = v2.defaults(rr=1.0, hours=True, max_hold=96, fresh=96)

runs = {
    "VERDE 5M (referinta)":  (df5, green_5m),
    "VERDE 15M":             (df15, green_15m),
}
curves = {}
print("=" * 90)
print(f"{'Config':26s} {'Trades':>7s} {'WR':>7s} {'Final $':>13s} {'Return':>9s} "
      f"{'MaxDD':>8s} {'Sharpe':>7s}")
print("-" * 90)
for name, (d, p) in runs.items():
    t = v2.run(d, p)
    e = equity(t)
    curves[name] = e
    m = metrics(e)
    print(f"{name:26s} {m['n']:7d} {m['wr']:6.1f}% {m['final']:>13,.0f} "
          f"{m['ret']:>+8.1f}% {m['maxdd']:>7.1f}% {m['sharpe']:>7.2f}")
print("=" * 90)

# out-of-sample pt. 15M
dates = sorted(df15["date"].unique())
split = dates[int(len(dates) * 0.6)]
tr, te = df15[df15.date < split], df15[df15.date >= split]
print(f"\nOut-of-sample 15M (split {split}):")
for nm, d in [("TRAIN", tr), ("TEST(OOS)", te)]:
    m = metrics(equity(v2.run(d, green_15m)))
    print(f"  {nm:10s} n={m['n']:3d} WR={m['wr']:5.1f}% ret={m['ret']:+6.1f}% "
          f"maxDD={m['maxdd']:5.1f}% Sharpe={m['sharpe']:.2f}")

# ---- plot
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), height_ratios=[2.4, 1], sharex=True)
cols = {"VERDE 5M (referinta)": "#2ca02c", "VERDE 15M": "#d62728"}
for name, e in curves.items():
    ax1.plot(e["dt"], e["equity"], label=name, color=cols[name], lw=1.9)
ax1.axhline(START, color="black", ls="--", lw=0.8, alpha=0.5)
sd = pd.Timestamp(split)
for ax in (ax1, ax2):
    ax.axvline(sd, color="red", ls=":", lw=1.3, alpha=0.6)
ax1.text(sd, ax1.get_ylim()[1], "  split OOS", color="red", va="top", fontsize=9)
ax1.set_ylabel("Equity ($)")
ax1.set_title("FVG + LVN (doar ore lichide)  —  5M vs 15M  |  $100k, risc 1%/trade",
              fontsize=13, fontweight="bold")
ax1.legend(loc="upper left", fontsize=10)
ax1.grid(alpha=0.25)
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))

e = curves["VERDE 15M"]
peak = e["equity"].cummax()
dd = (e["equity"] - peak) / peak * 100
ax2.fill_between(e["dt"], dd, 0, color="#d62728", alpha=0.35)
ax2.set_ylabel("Drawdown %\n(15M)")
ax2.set_xlabel("Data")
ax2.grid(alpha=0.25)
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
fig.autofmt_xdate()
plt.tight_layout()
plt.savefig("equity_15m.png", dpi=130, bbox_inches="tight")
print("\nGrafic -> equity_15m.png")
