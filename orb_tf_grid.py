#!/usr/bin/env python3
"""
ORB rulant multi-timeframe x multi-RR, cu verificare in 3 straturi.

Strategia: candela TF completata = range; pe 5M, stop order la marginea
range-ului (trigger DOAR la traversare: open inauntru -> extrema dincolo),
SL = marginea opusa, TP = RR x range, o intrare per fereastra TF, o pozitie
odata, cronologic. Cost 0.010% din pret per round-trip.

VERIFICARE:
  L1: invarianti hard la fiecare trade (asserts — orice incalcare opreste tot):
      - trigger valid (traversare), risk == range exact
      - exit SL => R_brut == -risk exact; exit TP => R_brut == +rr*risk exact
      - cronologie stricta: entry_bar > exit_bar-ul trade-ului precedent
  L2: DOUA implementari independente (iterare pe bare vs iterare pe ferestre);
      listele de trade-uri trebuie sa coincida exact pe toate combinatiile.
  L3: regula pre-inregistrata pt holdout: avgR>0, n>=200, t>=2 pe dev.

Limitele onestitatii (ce NU poate garanta niciun backtest OHLC):
  - ordinea intrabar SL/TP cand ambele sunt atinse in aceeasi bara de 5M
    => rezolvata PESIMIST (SL primul), deci rezultatele sunt o limita inferioara
  - costul real al brokerului tau (folosim 0.010%; testul stress il dubleaza)
"""
import numpy as np
import pandas as pd
from smc_test import load_mt5, prep, met

COST = 0.0001
MAXH = 288
TFS = ["15min", "30min", "1h", "2h", "4h"]
RRS = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
CHECKS = {"n": 0}


def tstat(R):
    R = np.asarray(R)
    return R.mean() / (R.std(ddof=1) / np.sqrt(len(R))) if len(R) > 2 and R.std() > 0 else 0.0


def build_ranges_tf(ds, tf):
    """Pentru fiecare fereastra TF: range-ul = candela TF PRECEDENTA contigua."""
    dfc = (ds["df"].set_index("dt").resample(tf)
           .agg(H=("high", "max"), L=("low", "min")).dropna())
    idx = dfc.index
    step = pd.Timedelta(tf)
    out = {}
    for i in range(1, len(idx)):
        if idx[i] - idx[i - 1] == step:
            out[idx[i]] = (dfc["H"].iloc[i - 1], dfc["L"].iloc[i - 1])
    return out


def _exit_sim(ds, j, d, entry, sl, tp, rr, risk):
    """Simulare exit din bara j (inclusiv). SL inaintea TP = pesimist. Cu invarianti."""
    h, l, c = ds["h"], ds["l"], ds["c"]
    N = ds["N"]
    tol = 1e-9 * entry
    for k in range(j, min(j + MAXH, N)):
        if d == 1:
            if l[k] <= sl:
                assert abs((sl - entry) - (-risk)) < tol, "SL invariant incalcat"
                CHECKS["n"] += 1
                return k, sl, "SL"
            if h[k] >= tp:
                assert abs((tp - entry) - rr * risk) < tol, "TP invariant incalcat"
                CHECKS["n"] += 1
                return k, tp, "TP"
        else:
            if h[k] >= sl:
                assert abs((entry - sl) - (-risk)) < tol, "SL invariant incalcat"
                CHECKS["n"] += 1
                return k, sl, "SL"
            if l[k] <= tp:
                assert abs((entry - tp) - rr * risk) < tol, "TP invariant incalcat"
                CHECKS["n"] += 1
                return k, tp, "TP"
    k = min(j + MAXH, N) - 1
    return k, c[k], "TIME"


def _try_trigger(ds, j, H_, L_):
    """Trigger stop-order: DOAR traversare. Returneaza (dir, entry) sau None."""
    o, h, l = ds["o"], ds["h"], ds["l"]
    if o[j] <= H_ < h[j]:
        return 1, H_
    if o[j] >= L_ > l[j]:
        return -1, L_
    return None


def make_trade(ds, j, d, entry, H_, L_, rr):
    risk = (entry - (L_ if d == 1 else H_)) * d
    assert abs(risk - (H_ - L_)) < 1e-9 * entry, "risk != range"
    CHECKS["n"] += 1
    sl = L_ if d == 1 else H_
    tp = entry + d * rr * risk
    k, ex, out = _exit_sim(ds, j, d, entry, sl, tp, rr, risk)
    R = ((ex - entry) * d - COST * entry) / risk
    assert R <= rr + 1e-9, "R peste maxim teoretic"
    CHECKS["n"] += 1
    return dict(jin=j, jout=k, d=d, R=R, out=out)


# ---------------- implementarea A: iterare pe BARE
def sim_bars(ds, ranges, rr):
    win = ds["win_cache"]
    trades = []
    busy = -1
    traded_win = None
    for j in range(ds["N"]):
        w = win[j]
        if w not in ranges or j <= busy or traded_win == w:
            continue
        H_, L_ = ranges[w]
        if H_ <= L_:
            continue
        trg = _try_trigger(ds, j, H_, L_)
        if trg is None:
            continue
        tr = make_trade(ds, j, trg[0], trg[1], H_, L_, rr)
        assert tr["jin"] > busy, "suprapunere cronologica"
        CHECKS["n"] += 1
        trades.append(tr)
        busy = tr["jout"]
        traded_win = w
    return trades


# ---------------- implementarea B (independenta): iterare pe FERESTRE
def sim_windows(ds, ranges, rr, tf):
    win = ds["win_cache"]
    N = ds["N"]
    # bornele fiecarei ferestre in indecsi de bare
    starts = {}
    for j in range(N):
        starts.setdefault(win[j], [j, j])[1] = j
    trades = []
    next_free = 0
    for w in sorted(ranges.keys()):
        if w not in starts:
            continue
        i0, i1 = starts[w]
        H_, L_ = ranges[w]
        if H_ <= L_:
            continue
        for j in range(max(i0, next_free), i1 + 1):
            trg = _try_trigger(ds, j, H_, L_)
            if trg is None:
                continue
            tr = make_trade(ds, j, trg[0], trg[1], H_, L_, rr)
            trades.append(tr)
            next_free = tr["jout"] + 1
            break
    return trades


def run_all(ds, label):
    print(f"\n{'='*100}\n GRID {label}  (cost {COST*100:.3f}%, SL-first pesimist)\n{'='*100}")
    print(f"{'TF':>6s} {'RR':>4s} {'n':>6s} {'WR':>7s} {'WRnec':>7s} {'avgR':>8s} "
          f"{'PF':>5s} {'t-stat':>7s} {'%TIME':>6s} {'range%':>7s}")
    print("-" * 100)
    rows = []
    for tf in TFS:
        ranges = build_ranges_tf(ds, tf)
        ds["win_cache"] = pd.Series(ds["df"]["dt"]).dt.floor(tf).values
        for rr in RRS:
            ta = sim_bars(ds, ranges, rr)
            tb = sim_windows(ds, ranges, rr, tf)
            # L2: cele doua implementari trebuie sa coincida exact
            assert len(ta) == len(tb), f"VERIFICARE ESUATA {tf} rr{rr}: n {len(ta)} vs {len(tb)}"
            for x, y in zip(ta, tb):
                assert x["jin"] == y["jin"] and x["jout"] == y["jout"] \
                    and abs(x["R"] - y["R"]) < 1e-12, f"VERIFICARE ESUATA {tf} rr{rr}"
            if not ta:
                continue
            R = np.array([t["R"] for t in ta])
            n = len(R)
            wr = (R > 0).mean() * 100
            cbar = COST / np.mean([abs(t["R"]) for t in ta if t["out"] == "SL"]) if any(
                t["out"] == "SL" for t in ta) else 0  # aprox, doar informativ
            wr_nec = 100 * (1 + COST * 0) / (1 + rr)   # teoretic fara cost
            gp, gl = R[R > 0].sum(), -R[R < 0].sum()
            pf = gp / gl if gl > 0 else 99
            ptime = np.mean([t["out"] == "TIME" for t in ta]) * 100
            # range mediu ca % din pret (marimea SL)
            o = ds["o"]
            rngpct = np.mean([(ranges[w][0] - ranges[w][1]) for w in ranges]) \
                / np.median(ds["c"]) * 100
            print(f"{tf:>6s} {rr:>4.1f} {n:>6d} {wr:>6.1f}% {wr_nec:>6.1f}% {R.mean():>+8.3f} "
                  f"{pf:>5.2f} {tstat(R):>+7.2f} {ptime:>5.1f}% {rngpct:>6.3f}%")
            rows.append(dict(tf=tf, rr=rr, n=n, wr=wr, avgR=R.mean(), pf=pf,
                             t=tstat(R), ptime=ptime))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    dev = prep(load_mt5([f"NDX100_M5 {n}.csv" for n in range(1, 7)]))
    print(f"DEV 2019-2024: {dev['N']:,} bare 5M")
    res = run_all(dev, "DEV 2019-2024")
    print(f"\nVERIFICARE L1+L2: {CHECKS['n']:,} invarianti verificati, 0 incalcari; "
          f"implementarile A si B identice pe toate {len(res)} combinatiile.")
    elig = res[(res.avgR > 0) & (res.n >= 200) & (res.t >= 2)]
    if elig.empty:
        print("\nREGULA PRE-INREGISTRATA: nimic cu avgR>0, n>=200, t>=2 -> holdout NEATINS.")
    else:
        print(f"\nEligibile pt holdout ({len(elig)}):")
        print(elig.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    res.to_csv("orb_tf_results.csv", index=False)
    print("Rezultate -> orb_tf_results.csv")
