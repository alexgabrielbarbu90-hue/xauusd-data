#!/usr/bin/env python3
"""
Optimizare riguroasa a strategiei 5M (config verde: ore lichide).
Cauta pe TRAIN (primele 60% zile), raporteaza performanta OOS pe TEST.
Cache pe profile VP si FVG pentru viteza.
"""
import itertools, time
import numpy as np
import pandas as pd
from types import SimpleNamespace
import fvg_lvn_backtest as bt
import fvg_lvn_v2 as v2

df = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")
dates = sorted(df["date"].unique())
SPLIT = dates[int(len(dates) * 0.6)]

# --- precalcul global
hi, lo, cl = df["high"].values, df["low"].values, df["close"].values
hour = df["dt"].dt.hour.values
dvec = df["date"].values
dt_vec = df["dt"].values
N = len(df)
prevmap = {dates[i]: dates[i - 1] for i in range(1, len(dates))}

_prof_cache = {}
def profiles(bin_, lvn_pct, hvn_pct=80):
    key = (bin_, lvn_pct, hvn_pct)
    if key not in _prof_cache:
        _prof_cache[key] = {d: v2.day_profile(df[df["date"] == d], bin_, lvn_pct, hvn_pct)
                            for d in dates}
    return _prof_cache[key]

_fvg_cache = {}
def fvgs(min_gap):
    if min_gap not in _fvg_cache:
        _fvg_cache[min_gap] = bt.find_fvgs(df, min_gap)
    return _fvg_cache[min_gap]

_ema_cache = {}
def ema(span):
    if span not in _ema_cache:
        _ema_cache[span] = df["close"].ewm(span=span, adjust=False).mean().values
    return _ema_cache[span]


def fast_run(p):
    prof = profiles(p.bin, p.lvn_pct)
    fv = fvgs(p.min_gap)
    em = ema(p.ema) if p.trend else None
    trades = []
    busy = -1
    for (i, imp, z_bot, z_top) in fv:
        d = dvec[i]
        if d not in prevmap:
            continue
        lvn_b, hvn_b, lo_ref = prof[prevmap[d]]
        if not bt.zone_overlaps_lvn(z_bot, z_top, lvn_b, lo_ref, p.bin):
            continue
        eidx = None
        for j in range(i + 1, min(i + 1 + p.fresh, N)):
            if j <= busy:
                continue
            if p.hours and not (p.h0 <= hour[j] <= p.h1):
                continue
            if p.trend:
                if imp == 1 and cl[j] < em[j]:
                    continue
                if imp == -1 and cl[j] > em[j]:
                    continue
            if imp == 1 and lo[j] <= z_top:
                eidx, entry = j, min(z_top, hi[j]); break
            if imp == -1 and hi[j] >= z_bot:
                eidx, entry = j, max(z_bot, lo[j]); break
        if eidx is None:
            continue
        direction = imp
        sl = (z_bot - p.sl_buf) if direction == 1 else (z_top + p.sl_buf)
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp = None
        if p.hvn_exit and hvn_b:
            centers = [lo_ref + (b + 0.5) * p.bin for b in hvn_b]
            cc = [c for c in centers if c > entry + p.bin] if direction == 1 \
                 else [c for c in centers if c < entry - p.bin]
            if cc:
                tp = min(cc) if direction == 1 else max(cc)
        if tp is None:
            tp = entry + direction * p.rr * risk
        outcome = exit_px = None
        for k in range(eidx, min(eidx + p.max_hold, N)):
            if direction == 1:
                if lo[k] <= sl: outcome, exit_px = "SL", sl; break
                if hi[k] >= tp: outcome, exit_px = "TP", tp; break
            else:
                if hi[k] >= sl: outcome, exit_px = "SL", sl; break
                if lo[k] <= tp: outcome, exit_px = "TP", tp; break
        else:
            k = min(eidx + p.max_hold, N) - 1
            outcome, exit_px = "TIME", cl[k]
        net = (exit_px - entry) * direction - p.cost
        trades.append((dt_vec[eidx], d, net / risk, net))
        busy = k
    return trades


def metrics(trades):
    if not trades:
        return dict(n=0, wr=0, avgR=0, totR=0, pf=0, sharpe=0, ret=0, maxdd=0)
    R = np.array([t[2] for t in trades])
    # equity 1% compus
    eq = 100000.0; eqs = []
    for r in R:
        eq += r * 0.01 * eq; eqs.append(eq)
    eqs = np.array(eqs); peak = np.maximum.accumulate(eqs)
    maxdd = ((eqs - peak) / peak).min() * 100
    gp = R[R > 0].sum(); gl = -R[R < 0].sum()
    return dict(n=len(R), wr=(R > 0).mean() * 100, avgR=R.mean(), totR=R.sum(),
                pf=gp / gl if gl > 0 else 99.0,
                sharpe=R.mean() / R.std() * np.sqrt(len(R)) if R.std() > 0 else 0,
                ret=(eqs[-1] / 100000 - 1) * 100, maxdd=maxdd)


def split_metrics(trades):
    tr = [t for t in trades if t[1] < SPLIT]
    te = [t for t in trades if t[1] >= SPLIT]
    return metrics(tr), metrics(te), metrics(trades)


def dflt(**kw):
    p = SimpleNamespace(bin=2.0, lvn_pct=20, min_gap=1.5, rr=1.0, sl_buf=0.5,
                        max_hold=288, fresh=288, cost=0.30, ema=200, trend=False,
                        hours=True, h0=10, h1=23, hvn_exit=False)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


# ============================================================ GRID
GRID = dict(
    hwin=[(8, 23), (10, 23), (13, 21), (14, 20), (15, 19), (10, 19), (13, 23)],
    bin=[1.0, 2.0, 3.0],
    lvn_pct=[15, 20, 25],
    min_gap=[1.0, 1.5, 2.5],
    rr=[1.0, 1.5, 2.0],
    sl_buf=[0.25, 0.5],
)
combos = list(itertools.product(*GRID.values()))
print(f"Grid: {len(combos)} configuratii  |  split OOS: {SPLIT}")
t0 = time.time()
rows = []
for (hwin, bin_, lvn_pct, min_gap, rr, sl_buf) in combos:
    p = dflt(h0=hwin[0], h1=hwin[1], bin=bin_, lvn_pct=lvn_pct, min_gap=min_gap,
             rr=rr, sl_buf=sl_buf)
    tr, te, full = split_metrics(fast_run(p))
    rows.append(dict(hwin=f"{hwin[0]}-{hwin[1]}", bin=bin_, lvn=lvn_pct, gap=min_gap,
                     rr=rr, slb=sl_buf,
                     tr_n=tr["n"], tr_avgR=tr["avgR"], tr_pf=tr["pf"], tr_sharpe=tr["sharpe"],
                     te_n=te["n"], te_avgR=te["avgR"], te_pf=te["pf"], te_ret=te["ret"],
                     te_dd=te["maxdd"], f_ret=full["ret"], f_sharpe=full["sharpe"]))
print(f"Rulat in {time.time()-t0:.1f}s\n")

res = pd.DataFrame(rows)
# selectie DOAR pe train, cu minim de trade-uri
elig = res[(res.tr_n >= 60) & (res.te_n >= 30)].copy()
elig["tr_rank"] = elig["tr_sharpe"]   # metrica de selectie pe train

pd.set_option("display.width", 200)
cols = ["hwin","bin","lvn","gap","rr","slb","tr_n","tr_avgR","tr_pf","tr_sharpe",
        "te_n","te_avgR","te_pf","te_ret","te_dd"]
print("=== TOP 15 dupa SHARPE pe TRAIN (cu performanta lor OOS pe TEST) ===")
top = elig.sort_values("tr_rank", ascending=False).head(15)
print(top[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}"))

print("\n=== Cate din TOP-20-train raman POZITIVE pe TEST (robustete) ===")
top20 = elig.sort_values("tr_rank", ascending=False).head(20)
print(f"  Pozitive OOS (te_avgR>0): {(top20.te_avgR>0).sum()}/20  |  "
      f"PF>1 OOS: {(top20.te_pf>1).sum()}/20")

print("\n=== Cea mai ROBUSTA (bun pe train SI test, penalizat pt. discrepanta) ===")
elig["robust"] = elig[["tr_avgR","te_avgR"]].min(axis=1)  # min din cele doua
best = elig.sort_values("robust", ascending=False).head(8)
print(best[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}"))

res.to_csv("opt_results.csv", index=False)
print(f"\n{len(res)} rezultate -> opt_results.csv")
