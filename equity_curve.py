#!/usr/bin/env python3
"""
Curbe de equity pentru FVG+LVN — capital initial $100,000, risc 1% / trade (compus).
Compara 3 configuratii si marcheaza split-ul out-of-sample.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import fvg_lvn_v2 as v2
import fvg_lvn_backtest as bt

START = 100_000.0
RISK = 0.01  # 1% din capitalul curent per trade

df = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")
dates = sorted(df["date"].unique())
split_date = dates[int(len(dates) * 0.6)]

CONFIGS = {
    "Baseline (fara filtre)":        v2.defaults(rr=1.0),
    "Doar ore lichide (robust)":     v2.defaults(rr=1.0, hours=True),
    "Combo: fresh+trend+ore+HVN":    v2.defaults(rr=1.0, fresh=24, trend=True,
                                                 hours=True, hvn_exit=True),
}


def equity(trades):
    """Compune equity cu risc 1% din capitalul curent. Returneaza df cu timp+equity."""
    t = trades.sort_values("entry_dt").reset_index(drop=True)
    eq = START
    rows = []
    for _, r in t.iterrows():
        pnl = r["R"] * (RISK * eq)   # R multiplu * dolari riscati (1% din equity curent)
        eq += pnl
        rows.append({"dt": r["entry_dt"], "equity": eq, "R": r["R"]})
    return pd.DataFrame(rows)


def metrics(e):
    if e.empty:
        return {}
    ret = e["equity"].iloc[-1] / START - 1
    peak = e["equity"].cummax()
    dd = (e["equity"] - peak) / peak
    maxdd = dd.min()
    # sharpe pe seria de R (per trade), anualizat aproximativ (~nr trade/an)
    r = e["R"]
    sharpe = r.mean() / r.std() * np.sqrt(len(r)) if r.std() > 0 else 0
    wins = (r > 0).sum()
    return dict(final=e["equity"].iloc[-1], ret=ret * 100, maxdd=maxdd * 100,
                n=len(e), wr=wins / len(e) * 100, sharpe=sharpe)


# ---- calcul
curves = {name: equity(v2.run(df, p)) for name, p in CONFIGS.items()}

print(f"Capital initial: ${START:,.0f}  |  Risc: {RISK*100:.0f}% / trade (compus)")
print("=" * 92)
print(f"{'Configuratie':32s} {'Trades':>7s} {'WR':>7s} {'Final $':>13s} "
      f"{'Return':>9s} {'MaxDD':>8s} {'Sharpe':>7s}")
print("-" * 92)
for name, e in curves.items():
    m = metrics(e)
    print(f"{name:32s} {m['n']:7d} {m['wr']:6.1f}% {m['final']:>13,.0f} "
          f"{m['ret']:>+8.1f}% {m['maxdd']:>7.1f}% {m['sharpe']:>7.2f}")
print("=" * 92)

# ---- plot
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), height_ratios=[2.4, 1],
                               sharex=True)
colors = {"Baseline (fara filtre)": "#8c8c8c",
          "Doar ore lichide (robust)": "#2ca02c",
          "Combo: fresh+trend+ore+HVN": "#1f77b4"}

for name, e in curves.items():
    ax1.plot(e["dt"], e["equity"], label=name, color=colors[name], lw=1.8)

ax1.axhline(START, color="black", ls="--", lw=0.8, alpha=0.5)
sd = pd.Timestamp(split_date)
for ax in (ax1, ax2):
    ax.axvline(sd, color="red", ls=":", lw=1.4, alpha=0.7)
ax1.text(sd, ax1.get_ylim()[1], "  split out-of-sample", color="red",
         va="top", fontsize=9)
ax1.set_ylabel("Equity ($)")
ax1.set_title("FVG + LVN  —  XAUUSD 5M  |  Capital $100,000  |  Risc 1%/trade (compus)",
              fontsize=13, fontweight="bold")
ax1.legend(loc="upper left", fontsize=10)
ax1.grid(alpha=0.25)
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))

# drawdown pt. configul robust
e = curves["Doar ore lichide (robust)"]
peak = e["equity"].cummax()
dd = (e["equity"] - peak) / peak * 100
ax2.fill_between(e["dt"], dd, 0, color="#2ca02c", alpha=0.4)
ax2.set_ylabel("Drawdown %\n(ore lichide)")
ax2.set_xlabel("Data")
ax2.grid(alpha=0.25)
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
fig.autofmt_xdate()

plt.tight_layout()
plt.savefig("equity_curve.png", dpi=130, bbox_inches="tight")
print("\nGrafic -> equity_curve.png")
