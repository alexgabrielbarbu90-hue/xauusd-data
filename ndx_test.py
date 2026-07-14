#!/usr/bin/env python3
"""
Test strategie FVG+LVN pe date NDX100 M5 REALE din MT5 (2019-2024, ~6 ani).
- Volume Profile din TICKVOL (VOL real = 0, exact cazul MT5).
- Parametri ADAPTIVI: VP cu N bin-uri/zi (scale-free), gap/SL/cost ca % din pret
  (pretul creste 6900->20000 pe 6 ani, deci parametrii ficsi in $ nu merg).
- Logica identica cu configul optimizat pe aur (respingere din LVN, ore lichide).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import fvg_lvn_backtest as bt

START, RISK = 100_000.0, 0.01


# ---------- merge MT5
def load_ndx():
    parts = []
    for n in range(1, 7):
        d = pd.read_csv(f"NDX100_M5 {n}.csv", sep="\t")
        d.columns = [c.strip("<>").lower() for c in d.columns]
        d["dt"] = pd.to_datetime(d["date"] + " " + d["time"], format="%Y.%m.%d %H:%M:%S")
        parts.append(d)
    m = pd.concat(parts).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    m["volume"] = m["tickvol"]          # <-- tick volume ca proxy de volum
    m["date"] = m["dt"].dt.date
    return m[["dt", "date", "open", "high", "low", "close", "volume"]]


# ---------- parametri de referinta din aur (ca procent)
gold = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")
gref = gold["close"].median()
GAP_PCT = 1.5 / gref
SLBUF_PCT = 0.25 / gref
COST_PCT = 0.30 / gref
gold_daily_rng = gold.groupby("date").apply(
    lambda x: x["high"].max() - x["low"].min(), include_groups=False).median()
N_BINS = max(15, int(round(gold_daily_rng / 2.0)))   # ~ acelasi nr de bin-uri ca la aur
print(f"Referinta aur: pret median ${gref:.0f} | gap%={GAP_PCT*100:.3f} "
      f"SLbuf%={SLBUF_PCT*100:.4f} cost%={COST_PCT*100:.4f} | N_bins/zi={N_BINS}")


# ---------- profil adaptiv (N bin-uri pe range-ul zilei)
def day_profile(dd, n_bins, lvn_pct, hvn_pct=80):
    lo, hi = dd["low"].min(), dd["high"].max()
    if hi <= lo:
        return None
    w = (hi - lo) / n_bins
    vol = np.zeros(n_bins)
    for l, h, v in zip(dd["low"].values, dd["high"].values, dd["volume"].values):
        i0 = min(n_bins - 1, max(0, int((l - lo) / w)))
        i1 = min(n_bins - 1, max(0, int((h - lo) / w)))
        vol[i0:i1 + 1] += v / (i1 - i0 + 1)
    t = vol > 0
    if t.sum() < 3:
        return None
    lvn = set(i for i in range(n_bins) if t[i] and vol[i] <= np.percentile(vol[t], lvn_pct))
    hvn = set(i for i in range(n_bins) if t[i] and vol[i] >= np.percentile(vol[t], hvn_pct))
    return lvn, hvn, lo, hi, w


def overlaps(z_bot, z_top, prof):
    lvn, hvn, lo, hi, w = prof
    if z_top < lo or z_bot > hi:
        return False
    i0 = max(0, int((z_bot - lo) / w))
    i1 = min(len(range(int((hi - lo) / w) + 1)) - 1, int((z_top - lo) / w))
    for i in range(min(i0, i1), max(i0, i1) + 1):
        if i in lvn:
            return True
    return False


def run(df, rr=1.5, lvn_pct=25, h0=15, h1=19, hours=True,
        fresh=288, max_hold=288):
    dates = sorted(df["date"].unique())
    prof = {d: day_profile(df[df["date"] == d], N_BINS, lvn_pct) for d in dates}
    prev = {dates[i]: dates[i - 1] for i in range(1, len(dates))}
    hi, lo, cl = df["high"].values, df["low"].values, df["close"].values
    hour = df["dt"].dt.hour.values
    dvec = df["date"].values
    dt_v = df["dt"].values
    N = len(df)
    fv = bt.find_fvgs(df, 0.0)   # toate FVG, filtram gap ca % mai jos
    trades = []
    busy = -1
    for (i, imp, z_bot, z_top) in fv:
        if (z_top - z_bot) < GAP_PCT * cl[i]:
            continue
        d = dvec[i]
        if d not in prev or prof[prev[d]] is None:
            continue
        if not overlaps(z_bot, z_top, prof[prev[d]]):
            continue
        eidx = None
        for j in range(i + 1, min(i + 1 + fresh, N)):
            if j <= busy:
                continue
            if hours and not (h0 <= hour[j] <= h1):
                continue
            if imp == 1 and lo[j] <= z_top:
                eidx, entry = j, min(z_top, hi[j]); break
            if imp == -1 and hi[j] >= z_bot:
                eidx, entry = j, max(z_bot, lo[j]); break
        if eidx is None:
            continue
        sl = (z_bot - SLBUF_PCT * entry) if imp == 1 else (z_top + SLBUF_PCT * entry)
        # risc SEMNAT (fix audit): abs() crea castiguri fictive pe zone stale.
        risk = (entry - sl) * imp
        if risk <= 0:
            continue
        tp = entry + imp * rr * risk
        out = ex = None
        for k in range(eidx, min(eidx + max_hold, N)):
            if imp == 1:
                if lo[k] <= sl: out, ex = "SL", sl; break
                if hi[k] >= tp: out, ex = "TP", tp; break
            else:
                if hi[k] >= sl: out, ex = "SL", sl; break
                if lo[k] <= tp: out, ex = "TP", tp; break
        else:
            k = min(eidx + max_hold, N) - 1; out, ex = "TIME", cl[k]
        net = (ex - entry) * imp - COST_PCT * entry
        trades.append((dt_v[eidx], d, net / risk, net))
        busy = k
    return pd.DataFrame(trades, columns=["entry_dt", "date", "R", "net"])


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
        return dict(n=len(e), wr=0, avgR=0, ret=0, maxdd=0, sharpe=0, pf=0)
    peak = e["equity"].cummax(); dd = ((e["equity"] - peak) / peak).min() * 100
    R = e["R"]; gp = R[R > 0].sum(); gl = -R[R < 0].sum()
    return dict(n=len(e), wr=(R > 0).mean() * 100, avgR=R.mean(),
                ret=(e["equity"].iloc[-1] / START - 1) * 100, maxdd=dd,
                sharpe=R.mean() / R.std() * np.sqrt(len(R)) if R.std() > 0 else 0,
                pf=gp / gl if gl > 0 else 99)


df = load_ndx()
print(f"NDX merge: {len(df):,} bare  {df['dt'].min()} -> {df['dt'].max()}\n")

# --- config optimizat vs control
print("=" * 92)
print(f"{'Config':30s} {'Trades':>7s} {'WR':>6s} {'avgR':>7s} {'Return':>10s} "
      f"{'MaxDD':>8s} {'Sharpe':>7s} {'PF':>6s}")
print("-" * 92)
t_opt = run(df, hours=True)
t_ctl = run(df, hours=False)
for nm, t in [("Optimizat (ore 15-19)", t_opt), ("Control (fara ore)", t_ctl)]:
    m = met(equity(t))
    print(f"{nm:30s} {m['n']:7d} {m['wr']:5.1f}% {m['avgR']:>+7.3f} {m['ret']:>+9.1f}% "
          f"{m['maxdd']:>7.1f}% {m['sharpe']:>7.2f} {m['pf']:>6.2f}")
print("=" * 92)

# --- defalcare pe AN (regimuri diferite)
print("\nPERFORMANTA PE AN (config optimizat) — test de robustete pe regimuri:")
print(f"  {'An':6s} {'Trades':>7s} {'WR':>6s} {'avgR':>7s} {'Return an':>10s} {'MaxDD':>8s}")
t_opt["yr"] = pd.to_datetime(t_opt["entry_dt"]).dt.year
for yr in sorted(t_opt["yr"].unique()):
    sub = t_opt[t_opt.yr == yr]
    m = met(equity(sub))
    print(f"  {yr:6d} {m['n']:7d} {m['wr']:5.1f}% {m['avgR']:>+7.3f} {m['ret']:>+9.1f}% {m['maxdd']:>7.1f}%")

# --- equity plot: 2 panouri (log compus + risc fix non-compus)
def fixed_equity(t):
    """Risc FIX $1000/trade (1% din capitalul INITIAL) - fara compunere, vedere onesta."""
    if t.empty:
        return pd.DataFrame(columns=["dt", "equity"])
    t = t.sort_values("entry_dt").reset_index(drop=True)
    eq = START + (t["R"] * RISK * START).cumsum()
    return pd.DataFrame({"dt": t["entry_dt"].values, "equity": eq.values})

e = equity(t_opt); ec = equity(t_ctl)
fe = fixed_equity(t_opt); fec = fixed_equity(t_ctl)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9))
# panou 1: compus, scara LOG
ax1.plot(e["dt"], e["equity"], color="#1f77b4", lw=1.6, label="Optimizat (ore 15-19)")
ax1.plot(ec["dt"], ec["equity"], color="#8c8c8c", lw=1.3, label="Control (fara ore)")
ax1.set_yscale("log")
ax1.axhline(START, color="black", ls="--", lw=0.8, alpha=0.5)
ax1.set_title("NDX100 M5 REAL (MT5 tick volume) 2019-2024  —  COMPUS 1%/trade (scara LOG)",
              fontsize=12, fontweight="bold")
ax1.set_ylabel("Equity ($, log)"); ax1.legend(loc="upper left"); ax1.grid(alpha=0.25, which="both")
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))
# panou 2: risc fix (non-compus) - vederea onesta
ax2.plot(fe["dt"], fe["equity"], color="#2ca02c", lw=1.6, label="Optimizat (risc fix $1k/trade)")
ax2.plot(fec["dt"], fec["equity"], color="#8c8c8c", lw=1.3, label="Control")
ax2.axhline(START, color="black", ls="--", lw=0.8, alpha=0.5)
ax2.set_title("Aceleasi trade-uri, RISC FIX $1000/trade (fara compunere) — vederea realista a edge-ului",
              fontsize=12, fontweight="bold")
ax2.set_ylabel("Equity ($)"); ax2.set_xlabel("Data"); ax2.legend(loc="upper left"); ax2.grid(alpha=0.25)
ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))
for ax in (ax1, ax2):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
plt.tight_layout()
plt.savefig("equity_ndx_real.png", dpi=130, bbox_inches="tight")
fixed_ret = (fe["equity"].iloc[-1] / START - 1) * 100
print(f"\nRisc FIX (non-compus): +{fixed_ret:.0f}% pe 6 ani = ~{fixed_ret/6:.0f}%/an mediu")
print("Grafic -> equity_ndx_real.png")
