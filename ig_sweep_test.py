#!/usr/bin/env python3
"""
Strategia din carusel (Pranam Ghagare): liquidity grab pe pivoti 15M + EMA30.

Reguli (fixate a priori, fidele postarii):
  - pivoti (1,1) pe 15M: low[i]<low[i-1] si low[i]<low[i+1] => pivot low
    (suport); confirmat la close-ul candelei i+1; semnalul de la candela k
    foloseste DOAR pivoti cu i <= k-2 (confirmati inainte de candela k)
  - sweep long: low[k] < suport si close[k] > suport si close[k] > EMA30(15M)
    (short simetric pe rezistenta, close[k] < EMA30)
  - intrare: BUY STOP la high-ul candelei de sweep; SL absolut la low; TP
    absolut = high + RR*(high-low); ordin plasat la close-ul candelei k,
    expira dupa 18 bare 5M (90 min); inlocuit de un semnal mai nou; ignorat
    daca suntem in pozitie (EA single-position)
  - fill pe 5M: traversare (open<=H<high) la nivelul H, sau GAP la open
    (cum executa MT5 un stop order; TP/SL raman absolute => RR efectiv <1 la
    gap-uri, exact ca live)
  - SL inaintea TP pe fiecare bara (pesimist); cost 0.010% round-trip
Invarianti: SL pierde exact riscul, chronologie stricta, risc > 0.
REGULA PRE-INREGISTRATA (NDX): holdout doar daca avgR>0, n>=200, t>=2.
"""
import numpy as np
import pandas as pd
import fvg_lvn_backtest as bt
from smc_test import load_mt5

COST = 0.0001
EXPIRY = 18            # bare 5M (90 min)
MAXH = 288
CHECKS = {"n": 0}


def tstat(R):
    R = np.asarray(R, float)
    return R.mean() / (R.std(ddof=1) / np.sqrt(len(R))) if len(R) > 2 and R.std() > 0 else 0.0


def signals_15m(df5, use_ema, ema_len=30):
    """Semnale sweep pe 15M -> lista (act_idx5, dir, H, L)."""
    d15 = (df5.set_index("dt").resample("15min")
           .agg(o=("open", "first"), h=("high", "max"),
                l=("low", "min"), c=("close", "last")).dropna())
    o, h, l, c = d15["o"].values, d15["h"].values, d15["l"].values, d15["c"].values
    idx = d15.index
    ema = d15["c"].ewm(span=ema_len, adjust=False).mean().values
    dt5 = df5["dt"].values
    n = len(d15)
    sig = []
    sup = res = None            # cel mai recent pivot confirmat (i <= k-2)
    for k in range(2, n):
        i = k - 2               # pivotul de la i e confirmat la close(i+1)=close(k-1)
        if 0 < i < n - 1:
            if l[i] < l[i - 1] and l[i] < l[i + 1]:
                sup = l[i]
            if h[i] > h[i - 1] and h[i] > h[i + 1]:
                res = h[i]
        if h[k] <= l[k]:
            continue
        long_ok = sup is not None and l[k] < sup and c[k] > sup \
            and (not use_ema or c[k] > ema[k])
        short_ok = res is not None and h[k] > res and c[k] < res \
            and (not use_ema or c[k] < ema[k])
        if not (long_ok or short_ok):
            continue
        act = np.searchsorted(dt5, np.datetime64(idx[k] + pd.Timedelta("15min")))
        if act >= len(dt5):
            continue
        if long_ok:
            sig.append((act, 1, h[k], l[k]))
        if short_ok:
            sig.append((act, -1, h[k], l[k]))
    sig.sort(key=lambda s: s[0])
    return sig


def run(df5, sig, rr):
    o5 = df5["open"].values
    h5 = df5["high"].values
    l5 = df5["low"].values
    c5 = df5["close"].values
    N = len(df5)
    dts = df5["dt"].values
    trades = []
    pend = None
    busy = -1
    p = 0
    for j in range(N):
        while p < len(sig) and sig[p][0] <= j:
            s = sig[p]
            p += 1
            if j <= busy:
                continue                     # in pozitie: semnal ignorat
            pend = (s[0], s[1], s[2], s[3])  # inlocuieste pending-ul vechi
        if j <= busy or pend is None:
            continue
        act, d, H, L = pend
        if j >= act + EXPIRY:
            pend = None
            continue
        rng = H - L
        if d == 1:
            if o5[j] >= H:
                entry, trig = o5[j], True        # gap fill la open
            elif o5[j] <= H < h5[j]:
                entry, trig = H, True            # traversare
            else:
                trig = False
        else:
            if o5[j] <= L:
                entry, trig = o5[j], True
            elif o5[j] >= L > l5[j]:
                entry, trig = L, True
            else:
                trig = False
        if not trig:
            continue
        pend = None
        sl = L if d == 1 else H
        tp = H + rr * rng if d == 1 else L - rr * rng   # ABSOLUTE (ca in MT5)
        risk = (entry - sl) * d
        assert risk > 0, "risc invalid"
        CHECKS["n"] += 1
        if (tp - entry) * d <= 0:            # gap dincolo de TP: exit imediat
            ex, k = entry, j
        else:
            ex = None
            k = j
            for k in range(j, min(j + MAXH, N)):
                if d == 1:
                    if l5[k] <= sl: ex = sl; break
                    if h5[k] >= tp: ex = tp; break
                else:
                    if h5[k] >= sl: ex = sl; break
                    if l5[k] <= tp: ex = tp; break
            if ex is None:
                k = min(j + MAXH, N) - 1
                ex = c5[k]
        gross = (ex - entry) * d
        if ex == sl:
            assert abs(gross + risk) < 1e-9 * entry, "SL invariant"
            CHECKS["n"] += 1
        assert k >= j and j > busy, "cronologie"
        CHECKS["n"] += 1
        trades.append(dict(dt=dts[j], R=(gross - COST * entry) / risk))
        busy = k
    return pd.DataFrame(trades)


def report(df5, label):
    print(f"\n=== {label} ===")
    print(f"{'config':>26s} {'semnale':>8s} {'n':>6s} {'WR':>7s} {'avgR':>8s} "
          f"{'PF':>5s} {'t-stat':>7s}")
    out = {}
    for name, use_ema, rr in [("as-posted: EMA30, RR1", True, 1.0),
                              ("control: fara EMA, RR1", False, 1.0),
                              ("control: EMA30, RR2", True, 2.0)]:
        sig = signals_15m(df5, use_ema)
        t = run(df5, sig, rr)
        if t.empty:
            print(f"{name:>26s} {len(sig):>8,} {0:>6d}")
            continue
        R = t["R"].values
        wr = (R > 0).mean() * 100
        gp, gl = R[R > 0].sum(), -R[R < 0].sum()
        pf = gp / gl if gl > 0 else 99
        print(f"{name:>26s} {len(sig):>8,} {len(R):>6d} {wr:>6.1f}% "
              f"{R.mean():>+8.3f} {pf:>5.2f} {tstat(R):>+7.2f}")
        out[name] = t
    return out


if __name__ == "__main__":
    # XAUUSD (instrumentul recomandat de autor)
    g = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")
    g5 = g[["dt", "open", "high", "low", "close"]].copy()
    rg = report(g5, "XAUUSD 5M (nov 2024 - nov 2025, ~1 an)")

    # NDX dev (masa statistica, 6 ani)
    nd = load_mt5([f"NDX100_M5 {n}.csv" for n in range(1, 7)])
    n5 = nd[["dt", "open", "high", "low", "close"]].copy()
    rn = report(n5, "NDX100 5M DEV (2019-2024)")

    print(f"\nInvarianti verificati: {CHECKS['n']:,}, 0 incalcari")
    tt = rn.get("as-posted: EMA30, RR1")
    if tt is not None and not tt.empty:
        tt = tt.copy()
        tt["yr"] = pd.to_datetime(tt["dt"]).dt.year
        ys = {int(y): f"n={len(gr)},avgR={gr.R.mean():+.2f}" for y, gr in tt.groupby("yr")}
        print(f"NDX as-posted pe ani: {ys}")
        R = tt["R"].values
        ok = R.mean() > 0 and len(R) >= 200 and tstat(R) >= 2
        print("\nREGULA PRE-INREGISTRATA (NDX): "
              + ("INDEPLINITA -> pot valida pe holdout" if ok
                 else "neindeplinita -> holdout NEATINS"))
