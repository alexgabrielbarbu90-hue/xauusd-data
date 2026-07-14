#!/usr/bin/env python3
"""
SMC: liquidity sweeps + order blocks pe NDX100 M5 (MT5).

Reguli de disciplina (post-audit):
  - motor corectat: risc SEMNAT + skip, procesare cronologica, o pozitie odata
  - intrare la OPEN-ul barei urmatoare semnalului (fara limit-in-zona -> fara
    selectie adversa si fara clasa de ordine invalide)
  - SL verificat INAINTEA TP pe fiecare bara (pesimist intrabar)
  - invariant: un exit pe SL nu poate avea gross > 0 (verificat la runtime)
  - dezvoltare DOAR pe 2019-2024; holdout-ul nov2024-mai2026 se atinge O DATA,
    la final, pentru configuratiile castigatoare pe dev
  - cost baza 0.010% din pret per round-trip (~1.8 pt idx la 20k, peste
    spread-ul median real din fereastra lichida); stress = 0.015% + slippage SL
"""
import numpy as np
import pandas as pd

MAXH = 288
BUF_PCT = 0.0002          # buffer SL: 0.02% din pret
COST_BASE = 0.0001        # 0.010% round-trip
COST_STRESS = 0.00015     # 0.015%
SLIP_STRESS = 0.0001      # 0.010% slippage pe SL (stress)
OB_LIFE = 288
DISP_K = 2.0              # corp > 2x median(96) = displacement


def load_mt5(files):
    parts = []
    for f in files:
        d = pd.read_csv(f, sep="\t")
        d.columns = [c.strip("<>").lower() for c in d.columns]
        d["dt"] = pd.to_datetime(d["date"] + " " + d["time"], format="%Y.%m.%d %H:%M:%S")
        parts.append(d)
    m = pd.concat(parts).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    m["day"] = m["dt"].dt.date
    return m


def prep(df):
    return dict(o=df["open"].values, h=df["high"].values, l=df["low"].values,
                c=df["close"].values, hour=df["dt"].dt.hour.values,
                day=df["day"].values, dt=df["dt"].values, N=len(df), df=df)


# ------------------------------------------------------------------ semnale
def sweeps_rolling(ds, look):
    """Raid peste swing high / sub swing low (rolling) + reclaim in aceeasi bara."""
    h, l, c = ds["h"], ds["l"], ds["c"]
    hh = pd.Series(h).rolling(look).max().shift(1).values
    ll = pd.Series(l).rolling(look).min().shift(1).values
    sig = []
    for j in range(look + 1, ds["N"]):
        if h[j] > hh[j] and c[j] < hh[j]:
            sig.append((j, -1, h[j]))          # short, SL ref = varful raidului
        elif l[j] < ll[j] and c[j] > ll[j]:
            sig.append((j, 1, l[j]))           # long
    return sig


def sweeps_pd(ds, conf=False, depth=0.0):
    """Sweep pe High/Low-ul ZILEI PRECEDENTE; doar primul raid pe fiecare parte/zi.
       conf=True: cere si inchiderea barei urmatoare inapoi sub/peste nivel."""
    h, l, c, day = ds["h"], ds["l"], ds["c"], ds["day"]
    df = ds["df"]
    g = df.groupby("day").agg(H=("high", "max"), L=("low", "min"))
    days = list(g.index)
    pdh = {days[i]: g["H"].iloc[i - 1] for i in range(1, len(days))}
    pdl = {days[i]: g["L"].iloc[i - 1] for i in range(1, len(days))}
    sig = []
    cur, done_hi, done_lo = None, False, False
    for j in range(ds["N"]):
        d = day[j]
        if d != cur:
            cur, done_hi, done_lo = d, False, False
        if d not in pdh:
            continue
        H, L = pdh[d], pdl[d]
        if not done_hi and h[j] > H + depth * c[j]:
            done_hi = True                      # primul raid peste PDH
            if not conf:
                if c[j] < H:
                    sig.append((j, -1, h[j]))
            elif j + 1 < ds["N"] and c[j + 1] < H:
                sig.append((j + 1, -1, max(h[j], h[j + 1])))
        if not done_lo and l[j] < L - depth * c[j]:
            done_lo = True
            if not conf:
                if c[j] > L:
                    sig.append((j, 1, l[j]))
            elif j + 1 < ds["N"] and c[j + 1] > L:
                sig.append((j + 1, 1, min(l[j], l[j + 1])))
    sig.sort(key=lambda s: s[0])
    return sig


def ob_pass(ds):
    """Un singur pas: zone Order Block active + semnale de retest cu confirmare.
       Returneaza (in_bull[j], in_bear[j], semnale_retest)."""
    o, h, l, c = ds["o"], ds["h"], ds["l"], ds["c"]
    N = ds["N"]
    body = np.abs(c - o)
    med = pd.Series(body).rolling(96).median().shift(1).values
    bull, bear = [], []          # zone: [zlo, zhi, born]
    in_bull = np.zeros(N, bool)
    in_bear = np.zeros(N, bool)
    retest = []
    for j in range(N):
        # expira / invalideaza
        bull = [z for z in bull if j - z[2] <= OB_LIFE and c[j] >= z[0]]
        bear = [z for z in bear if j - z[2] <= OB_LIFE and c[j] <= z[1]]
        # confluenta: bara atinge o zona
        hitb = [z for z in bull if j > z[2] + 1 and l[j] <= z[1] and h[j] >= z[0]]
        hits = [z for z in bear if j > z[2] + 1 and h[j] >= z[0] and l[j] <= z[1]]
        if hitb:
            in_bull[j] = True
            if c[j] > o[j]:                      # retest + inchidere de confirmare
                retest.append((j, 1, hitb[0][0]))
                bull.remove(hitb[0])             # o zona = un retest
        if hits:
            in_bear[j] = True
            if c[j] < o[j]:
                retest.append((j, -1, hits[0][1]))
                bear.remove(hits[0])
        # nastere zone noi (displacement)
        if j >= 96 and med[j] > 0 and body[j] > DISP_K * med[j]:
            if c[j] > o[j]:                      # impuls up -> ultimul candle rosu
                for b in range(j - 1, max(j - 6, -1), -1):
                    if c[b] < o[b]:
                        bull.append([l[b], h[b], j]); break
            else:
                for b in range(j - 1, max(j - 6, -1), -1):
                    if c[b] > o[b]:
                        bear.append([l[b], h[b], j]); break
    return in_bull, in_bear, retest


# ------------------------------------------------------------------ simulator
def simulate(ds, sig, rr, hours=None, cost_pct=COST_BASE, slip_pct=0.0):
    o, h, l, c = ds["o"], ds["h"], ds["l"], ds["c"]
    hour = ds["hour"]
    N = ds["N"]
    trades = []
    busy = -1
    for (j, d, slref) in sig:
        e = j + 1                                # intrare la open-ul barei urmatoare
        if e >= N or j <= busy:
            continue
        if hours and not (hours[0] <= hour[e] <= hours[1]):
            continue
        entry = o[e]
        buf = BUF_PCT * entry
        sl = slref - buf if d == 1 else slref + buf
        risk = (entry - sl) * d                  # SEMNAT; skip daca invalid
        if risk <= 0:
            continue
        tp = entry + d * rr * risk
        ex = None
        k = e
        for k in range(e, min(e + MAXH, N)):
            if d == 1:
                if l[k] <= sl: ex = sl - slip_pct * entry; break
                if h[k] >= tp: ex = tp; break
            else:
                if h[k] >= sl: ex = sl + slip_pct * entry; break
                if l[k] <= tp: ex = tp; break
        out = "SLTP"
        if ex is None:
            k = min(e + MAXH, N) - 1
            ex = c[k]
            out = "TIME"
        gross = (ex - entry) * d
        if out == "SLTP" and abs(ex - (sl - slip_pct * entry if d == 1 else sl + slip_pct * entry)) < 1e-9:
            assert gross < 0, "INVARIANT INCALCAT: SL cu profit!"
        trades.append(dict(dt=ds["dt"][e], R=(gross - cost_pct * entry) / risk))
        busy = k
    return pd.DataFrame(trades)


def met(t):
    if t.empty or len(t) < 2:
        return dict(n=len(t), wr=0, avgR=0, pf=0, maxdd=0)
    R = t.sort_values("dt")["R"].values
    eq = 100000.0
    eqs = []
    for r in R:
        eq += r * 0.01 * eq
        eqs.append(eq)
    eqs = np.array(eqs)
    peak = np.maximum.accumulate(eqs)
    gp, gl = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=len(R), wr=(R > 0).mean() * 100, avgR=R.mean(),
                pf=gp / gl if gl > 0 else 99, maxdd=((eqs - peak) / peak).min() * 100)


# ================================================================== DEV GRID
def run_grid():
    dev = prep(load_mt5([f"NDX100_M5 {n}.csv" for n in range(1, 7)]))
    print(f"DEV 2019-2024: {dev['N']:,} bare. Generez semnale...")
    in_bull, in_bear, ob_sig = ob_pass(dev)
    SIGSETS = {
        "sweep_roll24":  sweeps_rolling(dev, 24),
        "sweep_roll96":  sweeps_rolling(dev, 96),
        "sweep_PD":      sweeps_pd(dev),
        "sweep_PD_conf": sweeps_pd(dev, conf=True),
        "sweep_PD_deep": sweeps_pd(dev, depth=0.0005),
        "OB_retest":     ob_sig,
    }
    SIGSETS["sweep_PD+OB"] = [s for s in SIGSETS["sweep_PD"]
                              if (s[1] == 1 and in_bull[s[0]]) or (s[1] == -1 and in_bear[s[0]])]
    SIGSETS["sweep_roll96+OB"] = [s for s in SIGSETS["sweep_roll96"]
                                  if (s[1] == 1 and in_bull[s[0]]) or (s[1] == -1 and in_bear[s[0]])]
    for k, v in SIGSETS.items():
        print(f"  {k:18s}: {len(v):6,} semnale brute")

    print("\n" + "=" * 100)
    print(" GRID DEV (cost 0.010%, intrare open bara urmatoare, SL-first) — sortat dupa avgR")
    print("=" * 100)
    rows = []
    for name, sig in SIGSETS.items():
        for hours in [None, (15, 19)]:
            for rr in [1.5, 2.5]:
                t = simulate(dev, sig, rr, hours)
                m = met(t)
                rows.append(dict(sig=name, ore="15-19" if hours else "toate", rr=rr, **m))
    res = pd.DataFrame(rows).sort_values("avgR", ascending=False)
    pd.set_option("display.width", 130)
    print(res.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\nPozitive pe dev: {(res.avgR > 0).sum()}/{len(res)}  "
          f"(atentie: {len(res)} teste => cateva pot fi noroc)")

    top = res[(res.n >= 200) & (res.avgR > 0)].head(3)
    if not top.empty:
        print("\nStabilitate per-an (top dev, n>=200):")
        for _, r in top.iterrows():
            t = simulate(dev, SIGSETS[r["sig"]], r["rr"],
                         (15, 19) if r["ore"] == "15-19" else None)
            t["yr"] = pd.to_datetime(t["dt"]).dt.year
            ys = {yr: f"{g.R.mean():+.2f}" for yr, g in t.groupby("yr")}
            print(f"  {r['sig']} {r['ore']} rr{r['rr']}: {ys}")
    else:
        print("\nNicio configuratie pozitiva cu n>=200 pe dev -> nu ard holdout-ul.")
    res.to_csv("smc_dev_results.csv", index=False)
    print("\nRezultate -> smc_dev_results.csv")


if __name__ == "__main__":
    run_grid()
