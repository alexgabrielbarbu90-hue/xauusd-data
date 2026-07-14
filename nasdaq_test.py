#!/usr/bin/env python3
"""
Testeaza configul OPTIMIZAT (aur 5M) pe date NASDAQ (NQ futures, 5M, ~60 zile).
Parametrii de pret sunt scalati la volatilitatea NQ; orele/percentile se transfera direct.
"""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import fvg_lvn_v2 as v2
import fvg_lvn_backtest as bt

START, RISK = 100_000.0, 0.01


def parse_yahoo(path):
    j = json.load(open(path))
    r = j["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"],
                       "close": q["close"], "volume": q["volume"]})
    # UTC -> GMT+2 (acelasi referential ca aurul, fara DST)
    dt = pd.to_datetime(ts, unit="s", utc=True) + pd.Timedelta(hours=2)
    df["dt"] = dt.tz_localize(None)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df["volume"] > 0].reset_index(drop=True)
    df["date"] = df["dt"].dt.date
    return df


# --- scalare fata de aur
gold = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")
gold_range = (gold["high"] - gold["low"]).median()

nq = parse_yahoo("nq.json")
nq_range = (nq["high"] - nq["low"]).median()
scale = nq_range / gold_range
print(f"NQ: {len(nq):,} bare 5M  |  {nq['dt'].min()} -> {nq['dt'].max()}")
print(f"Range median 5M:  aur=${gold_range:.2f}  NQ=${nq_range:.2f}  ->  scale={scale:.1f}x\n")

GOLD = dict(bin=2.0, min_gap=1.5, sl_buf=0.25, cost=0.30)  # config optimizat aur
NQP = {k: v * scale for k, v in GOLD.items()}
print(f"Parametri scalati NQ: bin=${NQP['bin']:.1f}  gap=${NQP['min_gap']:.1f}  "
      f"SLbuf=${NQP['sl_buf']:.1f}  cost=${NQP['cost']:.1f}\n")


def cfg(hours, h0=15, h1=19):
    return v2.defaults(rr=1.5, hours=hours, h0=h0, h1=h1, lvn_pct=25,
                       bin=NQP["bin"], min_gap=NQP["min_gap"], sl_buf=NQP["sl_buf"],
                       cost=NQP["cost"], max_hold=288, fresh=288)


def equity(t):
    if t.empty:
        return pd.DataFrame(columns=["dt", "equity", "R"])
    t = t.sort_values("entry_dt").reset_index(drop=True)
    eq, rows = START, []
    for _, r in t.iterrows():
        eq += r["R"] * RISK * eq
        rows.append({"dt": r["entry_dt"], "equity": eq, "R": r["R"]})
    return pd.DataFrame(rows)


def met(e):
    if e.empty or len(e) < 2:
        return dict(n=len(e), wr=0, ret=0, maxdd=0, sharpe=0, pf=0, avgR=0)
    peak = e["equity"].cummax(); dd = ((e["equity"] - peak) / peak).min() * 100
    R = e["R"]; gp = R[R > 0].sum(); gl = -R[R < 0].sum()
    return dict(n=len(e), wr=(R > 0).mean() * 100, avgR=R.mean(),
                ret=(e["equity"].iloc[-1] / START - 1) * 100, maxdd=dd,
                sharpe=R.mean() / R.std() * np.sqrt(len(R)) if R.std() > 0 else 0,
                pf=gp / gl if gl > 0 else 99)


runs = {
    "NQ  config optimizat (ore 15-19)": cfg(True),
    "NQ  fara filtru ore (control)":    cfg(False),
    "NQ  ore US extins (15-22)":        cfg(True, 15, 22),
}
print("=" * 96)
print(f"{'Config':36s} {'Trades':>7s} {'WR':>6s} {'avgR':>7s} {'Return':>9s} "
      f"{'MaxDD':>8s} {'Sharpe':>7s} {'PF':>6s}")
print("-" * 96)
curves = {}
for name, p in runs.items():
    e = equity(v2.run(nq, p)); curves[name] = e; m = met(e)
    print(f"{name:36s} {m['n']:7d} {m['wr']:5.1f}% {m['avgR']:>+7.3f} {m['ret']:>+8.1f}% "
          f"{m['maxdd']:>7.1f}% {m['sharpe']:>7.2f} {m['pf']:>6.2f}")
print("=" * 96)

# mini train/test in cadrul celor 60 zile
main = curves["NQ  config optimizat (ore 15-19)"]
t_all = v2.run(nq, cfg(True))
if not t_all.empty:
    dts = sorted(nq["date"].unique()); sp = dts[int(len(dts) * 0.6)]
    for nm, sub in [("TRAIN 60%", t_all[t_all.date < sp]), ("TEST 40% (OOS)", t_all[t_all.date >= sp])]:
        m = met(equity(sub))
        print(f"  {nm:16s} n={m['n']:3d} WR={m['wr']:5.1f}% avgR={m['avgR']:+.3f} "
              f"ret={m['ret']:+6.1f}% Sharpe={m['sharpe']:.2f}")

# plot
plt.figure(figsize=(12, 6))
cols = {"NQ  config optimizat (ore 15-19)": "#1f77b4",
        "NQ  fara filtru ore (control)": "#8c8c8c",
        "NQ  ore US extins (15-22)": "#ff7f0e"}
for name, e in curves.items():
    if not e.empty:
        plt.plot(e["dt"], e["equity"], label=name, color=cols[name], lw=1.8)
plt.axhline(START, color="black", ls="--", lw=0.8, alpha=0.5)
plt.title("FVG + LVN pe NASDAQ (NQ futures 5M, ~60 zile)  |  config optimizat pe aur, parametri scalati",
          fontsize=12, fontweight="bold")
plt.ylabel("Equity ($)"); plt.xlabel("Data")
plt.legend(loc="upper left"); plt.grid(alpha=0.25)
plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
plt.gcf().autofmt_xdate()
plt.tight_layout()
plt.savefig("equity_nasdaq.png", dpi=130, bbox_inches="tight")
print("\nGrafic -> equity_nasdaq.png")
