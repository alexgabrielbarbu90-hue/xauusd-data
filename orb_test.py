#!/usr/bin/env python3
"""
ORB rulant: fiecare candela 15M completata = range; breakout pe 5M LA ATINGERE
(stop order la marginea range-ului, nu pe close). SL = partea opusa a range-ului.
TP = RR x range. O intrare per range, o pozitie odata, cronologic.

Interpretari RR testate: 0.5 (cerut, "1:05"), 1.0, 5.0 (citirea alternativa "1:5").

REGULA PRE-INREGISTRATA (inainte de a vedea rezultatele): holdout-ul se atinge
doar daca pe dev exista config cu avgR>0, n>=200 si t-stat>=2.

Pesimism intrabar: SL verificat inaintea TP pe fiecare bara, inclusiv bara
intrarii. TP pe bara intrarii e legitim (intrarea e la prima atingere a
marginii; high>=TP implica atingerea TP dupa intrare), dar cedeaza la SL-first.
"""
import numpy as np
import pandas as pd
from smc_test import load_mt5, prep, met

COST_BASE = 0.0001      # 0.010% round-trip
COST_STRESS = 0.00015
SLIP_STRESS = 0.00005   # 0.005% pe intrare (stop order) SI pe SL
MAXH = 288


def tstat(t):
    if len(t) < 3:
        return 0.0
    R = t["R"].values
    return R.mean() / (R.std(ddof=1) / np.sqrt(len(R))) if R.std() > 0 else 0.0


def build_ranges(ds):
    """Range-ul activ in fereastra w = candela 15M din fereastra precedenta
       (doar daca e contigua, 15 min inainte)."""
    df15 = (ds["df"].set_index("dt")
            .resample("15min").agg(H=("high", "max"), L=("low", "min")).dropna())
    idx = df15.index
    r15 = (df15["H"] - df15["L"])
    rmed = r15.rolling(96).median()          # ~1 zi de candele 15M
    rngH, rngL, rbig = {}, {}, {}
    for i in range(1, len(idx)):
        if (idx[i] - idx[i - 1]) == pd.Timedelta("15min"):
            rngH[idx[i]] = df15["H"].iloc[i - 1]
            rngL[idx[i]] = df15["L"].iloc[i - 1]
            m = rmed.iloc[i - 1]
            rbig[idx[i]] = bool(r15.iloc[i - 1] > m) if np.isfinite(m) else False
    return rngH, rngL, rbig


def run_orb(ds, rng_data, rr, hours=None, min_range=False,
            cost_pct=COST_BASE, slip_pct=0.0):
    o, h, l, c, hour = ds["o"], ds["h"], ds["l"], ds["c"], ds["hour"]
    N = ds["N"]
    rngH, rngL, rbig = rng_data
    win = pd.Series(ds["df"]["dt"]).dt.floor("15min").values
    trades = []
    busy = -1
    traded_win = None
    for j in range(N):
        w = win[j]
        if w not in rngH or j <= busy or traded_win == w:
            continue
        if min_range and not rbig[w]:
            continue
        if hours and not (hours[0] <= hour[j] <= hours[1]):
            continue
        H_, L_ = rngH[w], rngL[w]
        rng = H_ - L_
        if rng <= 0:
            continue
        # trigger DOAR la traversarea nivelului (open inauntru) — asa functioneaza
        # un stop order real; fara asta, barele care deschid dincolo de nivel
        # (dupa busy/reintrare) ar primi fill fictiv la nivel = optimism sistematic
        if o[j] <= H_ < h[j]:
            d, entry = 1, H_
        elif o[j] >= L_ > l[j]:
            d, entry = -1, L_
        else:
            continue
        traded_win = w
        entry_eff = entry + d * slip_pct * entry      # slippage pe stop-entry
        sl = L_ if d == 1 else H_
        risk = (entry_eff - sl) * d
        if risk <= 0:
            continue
        tp = entry_eff + d * rr * risk
        ex = None
        k = j
        for k in range(j, min(j + MAXH, N)):
            if d == 1:
                if l[k] <= sl: ex = sl - slip_pct * entry; break
                if h[k] >= tp: ex = tp; break
            else:
                if h[k] >= sl: ex = sl + slip_pct * entry; break
                if l[k] <= tp: ex = tp; break
        if ex is None:
            k = min(j + MAXH, N) - 1
            ex = c[k]
        gross = (ex - entry_eff) * d
        trades.append(dict(dt=ds["dt"][j], R=(gross - cost_pct * entry) / risk))
        busy = k
    return pd.DataFrame(trades)


if __name__ == "__main__":
    dev = prep(load_mt5([f"NDX100_M5 {n}.csv" for n in range(1, 7)]))
    rng = build_ranges(dev)
    print(f"DEV 2019-2024: {dev['N']:,} bare | range-uri 15M valide: {len(rng[0]):,}")
    print(f"Breakeven WR teoretic (fara cost): RR0.5 -> 66.7% | RR1 -> 50% | RR5 -> 16.7%\n")
    print("=" * 98)
    print(f"{'config':34s} {'n':>6s} {'WR':>7s} {'WRnec':>6s} {'avgR':>7s} "
          f"{'PF':>5s} {'t-stat':>7s} {'maxDD':>8s}")
    print("-" * 98)
    rows = []
    for rr in [0.5, 1.0, 5.0]:
        for hours in [None, (15, 19)]:
            for mr in [False, True]:
                t = run_orb(dev, rng, rr, hours, mr)
                m = met(t)
                wr_nec = 100 / (1 + rr)
                nm = (f"RR{rr} {'15-19' if hours else 'toate'}"
                      f"{' range>med' if mr else ''}")
                print(f"{nm:34s} {m['n']:6d} {m['wr']:6.1f}% {wr_nec:5.1f}% "
                      f"{m['avgR']:+7.3f} {m['pf']:5.2f} {tstat(t):+7.2f} {m['maxdd']:7.1f}%")
                rows.append(dict(cfg=nm, rr=rr, ore=bool(hours), mr=mr, t=tstat(t), **m))
    res = pd.DataFrame(rows)
    elig = res[(res.avgR > 0) & (res.n >= 200) & (res.t >= 2)]
    print("=" * 98)
    if elig.empty:
        print("\nREGULA PRE-INREGISTRATA: nicio configuratie cu avgR>0, n>=200, t>=2"
              " pe dev -> holdout-ul NU se atinge.")
    else:
        print(f"\n{len(elig)} configuratii indeplinesc regula -> eligibile pt holdout:")
        print(elig[["cfg", "n", "avgR", "t"]].to_string(index=False))
    # stress pe configul cerut explicit (RR 0.5, toate orele)
    ts = run_orb(dev, rng, 0.5, None, False, COST_STRESS, SLIP_STRESS)
    ms = met(ts)
    print(f"\nConfigul cerut (RR0.5) sub STRESS (cost 0.015% + slip 0.005%): "
          f"n={ms['n']} WR={ms['wr']:.1f}% avgR={ms['avgR']:+.3f} PF={ms['pf']:.2f}")
