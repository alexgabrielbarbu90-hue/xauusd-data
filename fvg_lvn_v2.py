#!/usr/bin/env python3
"""
FVG + LVN v2 — cu filtre de confluenta, exit la HVN, si validare out-of-sample.

Pargii testate:
  1. Filtre: FVG proaspete, trend zilnic (EMA200), ore lichide (London/NY).
  2. Exit: TP la primul HVN (nod volum mare) in loc de R fix.
  3. SL/TP: sweep pe buffer si RR.
  4. Robustete: split train (prima parte) / test (out-of-sample).
"""
import numpy as np
import pandas as pd
from types import SimpleNamespace
import fvg_lvn_backtest as bt


def day_profile(day_df, bin_size, lvn_pct, hvn_pct):
    lo = np.floor(day_df["low"].min() / bin_size) * bin_size
    hi = np.ceil(day_df["high"].max() / bin_size) * bin_size
    if hi <= lo:
        return set(), set(), lo
    nb = int(round((hi - lo) / bin_size))
    vol = np.zeros(nb)
    for l, h, v in zip(day_df["low"].values, day_df["high"].values, day_df["volume"].values):
        i0 = max(0, min(nb - 1, int((l - lo) // bin_size)))
        i1 = max(0, min(nb - 1, int((h - lo) // bin_size)))
        n = i1 - i0 + 1
        vol[i0:i1 + 1] += v / n
    touched = vol > 0
    if touched.sum() < 3:
        return set(), set(), lo
    lvn_t = np.percentile(vol[touched], lvn_pct)
    hvn_t = np.percentile(vol[touched], hvn_pct)
    lvn = set(i for i in range(nb) if touched[i] and vol[i] <= lvn_t)
    hvn = set(i for i in range(nb) if touched[i] and vol[i] >= hvn_t)
    return lvn, hvn, lo


def run(df, p):
    df = df.copy()
    df["ema"] = df["close"].ewm(span=p.ema, adjust=False).mean()
    df["hour"] = df["dt"].dt.hour

    dates = sorted(df["date"].unique())
    prof = {}
    for d in dates:
        prof[d] = day_profile(df[df["date"] == d], p.bin, p.lvn_pct, p.hvn_pct)
    prev = {dates[i]: dates[i - 1] for i in range(1, len(dates))}

    hi, lo, cl = df["high"].values, df["low"].values, df["close"].values
    ema, hour = df["ema"].values, df["hour"].values
    n = len(df)
    fvgs = bt.find_fvgs(df, p.min_gap)

    trades = []
    busy = -1
    for (i, imp, z_bot, z_top) in fvgs:
        d = df["date"].iloc[i]
        if d not in prev:
            continue
        lvn_bins, hvn_bins, lo_ref = prof[prev[d]]
        if not bt.zone_overlaps_lvn(z_bot, z_top, lvn_bins, lo_ref, p.bin):
            continue

        # intrare pe retrasare (fresh window)
        eidx = None
        for j in range(i + 1, min(i + 1 + p.fresh, n)):
            if j <= busy:
                continue
            # filtru ore lichide
            if p.hours and not (p.h0 <= hour[j] <= p.h1):
                continue
            # filtru trend
            if p.trend:
                if imp == 1 and cl[j] < ema[j]:
                    continue
                if imp == -1 and cl[j] > ema[j]:
                    continue
            if imp == 1 and lo[j] <= z_top:
                eidx, entry = j, min(z_top, hi[j])
                break
            if imp == -1 and hi[j] >= z_bot:
                eidx, entry = j, max(z_bot, lo[j])
                break
        if eidx is None:
            continue

        direction = imp
        if direction == 1:
            sl = z_bot - p.sl_buf
        else:
            sl = z_top + p.sl_buf
        risk = abs(entry - sl)
        if risk <= 0:
            continue

        # TP: HVN sau R fix
        tp = None
        if p.hvn_exit and hvn_bins:
            centers = [lo_ref + (b + 0.5) * p.bin for b in hvn_bins]
            if direction == 1:
                cands = [c for c in centers if c > entry + p.bin]
                if cands:
                    tp = min(cands)
            else:
                cands = [c for c in centers if c < entry - p.bin]
                if cands:
                    tp = max(cands)
        if tp is None:
            tp = entry + direction * p.rr * risk
        # asigura RR minim
        if direction == 1 and tp <= entry:
            tp = entry + p.rr * risk
        if direction == -1 and tp >= entry:
            tp = entry - p.rr * risk

        outcome, exit_px = None, None
        for k in range(eidx, min(eidx + p.max_hold, n)):
            if direction == 1:
                if lo[k] <= sl:
                    outcome, exit_px = "SL", sl; break
                if hi[k] >= tp:
                    outcome, exit_px = "TP", tp; break
            else:
                if hi[k] >= sl:
                    outcome, exit_px = "SL", sl; break
                if lo[k] <= tp:
                    outcome, exit_px = "TP", tp; break
        else:
            k = min(eidx + p.max_hold, n) - 1
            outcome, exit_px = "TIME", cl[k]

        gross = (exit_px - entry) * direction
        net = gross - p.cost
        trades.append({"date": d, "dir": direction, "R": net / risk,
                       "net": net, "outcome": outcome, "risk": risk})
        busy = k
    return pd.DataFrame(trades)


def stats(t):
    if t.empty:
        return dict(n=0, wr=0, avgR=0, totR=0, pf=0)
    n = len(t); w = (t.net > 0).sum()
    gp = t.loc[t.net > 0, "net"].sum(); gl = -t.loc[t.net < 0, "net"].sum()
    return dict(n=n, wr=w / n * 100, avgR=t.R.mean(), totR=t.R.sum(),
                pf=gp / gl if gl > 0 else 99.0)


def defaults(**kw):
    p = SimpleNamespace(bin=2.0, lvn_pct=20, hvn_pct=80, min_gap=1.5, rr=2.0,
                        sl_buf=0.5, max_hold=288, fresh=288, cost=0.30, ema=200,
                        trend=False, hours=False, h0=10, h1=23, hvn_exit=False)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


if __name__ == "__main__":
    df = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")

    def line(name, p):
        s = stats(run(df, p))
        print(f"  {name:36s} n={s['n']:4d}  WR={s['wr']:5.1f}%  "
              f"avgR={s['avgR']:+.3f}  PF={s['pf']:.2f}  totR={s['totR']:+.1f}")

    print("=" * 78)
    print(" IZOLAREA FIECAREI PARGHII (cost real $0.30, baseline: bin2/gap1.5/rr1)")
    print("=" * 78)
    base = dict(rr=1.0)
    line("0. Baseline (fara filtre)", defaults(**base))
    line("1a. + FVG proaspat (<=24 bare/2h)", defaults(**base, fresh=24))
    line("1b. + Filtru trend (EMA200)", defaults(**base, trend=True))
    line("1c. + Ore lichide (10-23 GMT+2)", defaults(**base, hours=True))
    line("2.  + Exit la HVN (RR variabil)", defaults(rr=1.0, hvn_exit=True))
    line("COMBO fresh+trend+ore", defaults(**base, fresh=24, trend=True, hours=True))
    line("COMBO fresh+trend+ore+HVN", defaults(rr=1.0, fresh=24, trend=True, hours=True, hvn_exit=True))

    print("\n" + "=" * 78)
    print(" SWEEP SL/TP pe COMBO (fresh24+trend+ore)")
    print("=" * 78)
    best = None
    for sl_buf in [0.25, 0.5, 1.0]:
        for rr in [1.0, 1.5, 2.0, 3.0]:
            p = defaults(fresh=24, trend=True, hours=True, sl_buf=sl_buf, rr=rr)
            s = stats(run(df, p))
            if s["n"] >= 30:
                print(f"  sl_buf={sl_buf}  rr={rr}  ->  n={s['n']:3d}  "
                      f"WR={s['wr']:5.1f}%  avgR={s['avgR']:+.3f}  PF={s['pf']:.2f}")
                if best is None or s["avgR"] > best[1]["avgR"]:
                    best = ((sl_buf, rr), s, p)

    print("\n" + "=" * 78)
    print(" VALIDARE OUT-OF-SAMPLE (train = prima 60% zile, test = ultimele 40%)")
    print("=" * 78)
    dates = sorted(df["date"].unique())
    split = dates[int(len(dates) * 0.6)]
    df_tr = df[df["date"] < split]
    df_te = df[df["date"] >= split]
    print(f"  Split la {split}  |  train zile<{split}, test zile>=")
    if best:
        (sl_buf, rr), _, p = best
        print(f"  Config castigatoare din sweep: sl_buf={sl_buf} rr={rr} +fresh24+trend+ore")
        for name, d in [("TRAIN", df_tr), ("TEST (out-of-sample)", df_te)]:
            s = stats(run(d, p))
            print(f"    {name:22s} n={s['n']:3d}  WR={s['wr']:5.1f}%  "
                  f"avgR={s['avgR']:+.3f}  PF={s['pf']:.2f}  totR={s['totR']:+.1f}")
