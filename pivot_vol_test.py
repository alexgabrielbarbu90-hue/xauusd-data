#!/usr/bin/env python3
"""
Breakout de pivoti 15M cu filtru de volum, executie pe 5M.

Reguli (fixate a priori):
  - pivot high/low pe 15M: extrema strict mai mare/mica decat N=3 candele pe
    fiecare parte; nivelul devine ACTIV abia la close-ul candelei i+3 (fara
    lookahead); viata maxima 2 zile (576 bare 5M); nivelul se CONSUMA la
    primul close de 5M dincolo de el (tradat sau nu — fara re-cross stale)
  - semnal: close 5M dincolo de nivel + rvol >= prag; intrare la OPEN-ul
    barei urmatoare (volumul barei de confirmare e cunoscut abia la close)
  - rvol = tickvol / mediana aceluiasi slot orar 5M din ultimele 20 zile
    (normalizare time-of-day; fara ea "volum mare" = doar "e ora NY")
  - anti-chasing: skip daca intrarea e extinsa >0.15% peste nivel
  - SL = nivelul pivotului -/+ buffer (breakout esuat = invalidare)
  - motor: smc_test.simulate (validat anterior: next-open entry, risc semnat,
    SL-first pesimist, cronologic, o pozitie odata)

REGULA PRE-INREGISTRATA: holdout doar daca avgR>0, n>=200, t>=2 pe dev.
"""
import numpy as np
import pandas as pd
from collections import deque, defaultdict
from smc_test import load_mt5, prep, simulate, met

N_PIV = 3
LIFE = 576            # bare 5M (~2 zile)
EXT_MAX = 0.0015      # anti-chasing: max 0.15% peste nivel
RVOL_DAYS = 20


def tstat(t):
    if len(t) < 3:
        return 0.0
    R = t["R"].values
    return R.mean() / (R.std(ddof=1) / np.sqrt(len(R))) if R.std() > 0 else 0.0


def rvol_series(ds):
    """rvol[j] = tickvol[j] / mediana slotului (HH:MM) din ultimele 20 de zile."""
    df = ds["df"]
    vol = df["tickvol"].values.astype(float)
    slots = (df["dt"].dt.hour * 12 + df["dt"].dt.minute // 5).values
    days = df["day"].values
    hist = defaultdict(lambda: deque(maxlen=RVOL_DAYS))
    rvol = np.full(len(df), np.nan)
    cur = None
    today = []
    for j in range(len(df)):
        if days[j] != cur:
            for s, v in today:               # commit ziua precedenta
                hist[s].append(v)
            today = []
            cur = days[j]
        h = hist[slots[j]]
        if len(h) >= 10:
            med = np.median(h)
            if med > 0:
                rvol[j] = vol[j] / med
        today.append((slots[j], vol[j]))
    return rvol


def pivots(ds):
    """Pivoti 15M cu index de activare in bare 5M (close-ul candelei i+N)."""
    df15 = (ds["df"].set_index("dt").resample("15min")
            .agg(H=("high", "max"), L=("low", "min")).dropna())
    Hs, Ls, idx = df15["H"].values, df15["L"].values, df15.index
    dt5 = ds["df"]["dt"].values
    out = []                                  # (activ_idx5, 'H'/'L', nivel)
    for i in range(N_PIV, len(df15) - N_PIV):
        conf_time = idx[i + N_PIV] + pd.Timedelta("15min")   # close candela i+N
        a = np.searchsorted(dt5, np.datetime64(conf_time))
        if a >= len(dt5):
            continue
        if all(Hs[i] > Hs[i - k] for k in range(1, N_PIV + 1)) and \
           all(Hs[i] > Hs[i + k] for k in range(1, N_PIV + 1)):
            out.append((a, "H", Hs[i]))
        if all(Ls[i] < Ls[i - k] for k in range(1, N_PIV + 1)) and \
           all(Ls[i] < Ls[i + k] for k in range(1, N_PIV + 1)):
            out.append((a, "L", Ls[i]))
    out.sort(key=lambda x: x[0])
    return out


def gen_signals(ds, piv, rvol, thr):
    """Semnale (j, dir, sl_ref). Nivel consumat la primul close dincolo de el."""
    c = ds["cl"] if "cl" in ds else ds["c"]
    N = ds["N"]
    sig = []
    act_h, act_l = [], []
    p = 0
    for j in range(N):
        while p < len(piv) and piv[p][0] <= j:
            (act_h if piv[p][1] == "H" else act_l).append((piv[p][0], piv[p][2]))
            p += 1
        if act_h:
            act_h = [x for x in act_h if j - x[0] <= LIFE]
        if act_l:
            act_l = [x for x in act_l if j - x[0] <= LIFE]
        px = c[j]
        for x in list(act_h):
            if px > x[1]:
                act_h.remove(x)              # consumat, tradat sau nu
                if np.isfinite(rvol[j]) and rvol[j] >= thr \
                        and (px - x[1]) / px <= EXT_MAX:
                    sig.append((j, 1, x[1]))
        for x in list(act_l):
            if px < x[1]:
                act_l.remove(x)
                if np.isfinite(rvol[j]) and rvol[j] >= thr \
                        and (x[1] - px) / px <= EXT_MAX:
                    sig.append((j, -1, x[1]))
    return sig


if __name__ == "__main__":
    dev = prep(load_mt5([f"NDX100_M5 {n}.csv" for n in range(1, 7)]))
    dev["c"] = dev["cl"] if "cl" in dev else dev["df"]["close"].values
    print(f"DEV 2019-2024: {dev['N']:,} bare 5M")
    rv = rvol_series(dev)
    pv = pivots(dev)
    print(f"Pivoti 15M gasiti (N={N_PIV}): {len(pv):,}  "
          f"(H: {sum(1 for x in pv if x[1]=='H'):,} / L: {sum(1 for x in pv if x[1]=='L'):,})")
    print(f"rvol definit pe {np.isfinite(rv).mean()*100:.0f}% din bare\n")

    print("=" * 96)
    print(f"{'filtru volum':>14s} {'RR':>4s} {'n':>6s} {'WR':>7s} {'avgR':>8s} "
          f"{'PF':>5s} {'t-stat':>7s}")
    print("-" * 96)
    rows = []
    for thr, lbl in [(0.0, "fara filtru"), (1.5, "rvol>=1.5"),
                     (2.0, "rvol>=2.0"), (3.0, "rvol>=3.0")]:
        sig = gen_signals(dev, pv, rv, thr)
        for rr in [1.5, 3.0]:
            t = simulate(dev, sig, rr, hours=None)
            m = met(t)
            ts = tstat(t)
            print(f"{lbl:>14s} {rr:>4.1f} {m['n']:>6d} {m['wr']:>6.1f}% "
                  f"{m['avgR']:>+8.3f} {m['pf']:>5.2f} {ts:>+7.2f}")
            rows.append(dict(thr=thr, rr=rr, t=ts, **m))
    res = pd.DataFrame(rows)
    print("=" * 96)
    elig = res[(res.avgR > 0) & (res.n >= 200) & (res.t >= 2)]
    if elig.empty:
        print("\nREGULA PRE-INREGISTRATA: nimic cu avgR>0, n>=200, t>=2 -> holdout NEATINS.")
    else:
        print(f"\nEligibile pt holdout:\n{elig.to_string(index=False)}")
