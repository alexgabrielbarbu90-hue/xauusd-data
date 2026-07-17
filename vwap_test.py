#!/usr/bin/env python3
"""
Strategie VWAP mean-reversion cu pozitie scalata pe distanta.

Regula: target_pozitie = -clip(deviatie/d_ref, -maxpos, +maxpos), unde
deviatie = (close - VWAP_sesiune)/VWAP. Sub VWAP => long (mai mult cu cat e
mai jos), peste => short simetric. Cuantizare 0.25 unitati (limiteaza
turnover-ul). 1 unitate = notional 100% din capitalul de referinta E.

Executie: decizia la close-ul barei t, fill la OPEN-ul barei t+1 (zero
lookahead prin constructie). Flat fortat la open-ul ultimei bare a zilei.
Cost: 0.005% pe fiecare parte din notionalul tranzactionat (0.010% RT).

VERIFICARE:
  L1 invarianti: |pozitie| <= maxpos, flat la finalul FIECAREI zile,
     costuri >= 0, fill-index > signal-index.
  L2 contabilitate dubla: P&L din ledger-ul de fill-uri (cash) trebuie sa
     coincida exact cu P&L mark-to-market, zi de zi.
  Control beta: regresie P&L zilnic ~ randament intraday piata => alpha, beta.
  Control semn inversat (momentum) pentru sanity.
  Regula pre-inregistrata holdout: mean>0 cu t>=2 pe P&L zilnic SI t>=2 pe alpha.
"""
import numpy as np
import pandas as pd
from smc_test import load_mt5

E = 100_000.0
COST_SIDE = 0.00005          # 0.005% pe parte
QUANT = 0.25


def daily_sim(day_df, d_ref, maxpos, flip=False):
    """Simuleaza o zi. Returneaza (pnl_net_$, cost_$, turnover_notional, mkt_ret)."""
    o = day_df["open"].values
    h = day_df["high"].values
    l = day_df["low"].values
    c = day_df["close"].values
    v = day_df["tickvol"].values.astype(float)
    n = len(day_df)
    if n < 20:
        return None
    tp = (h + l + c) / 3.0
    cum_pv = np.cumsum(tp * v)
    cum_v = np.cumsum(v)
    vwap = cum_pv / np.maximum(cum_v, 1e-12)

    shares = 0.0
    cash = 0.0                # ledger: -fill_price*dshares - fee
    mtm = 0.0                 # mark-to-market acumulat
    cost = 0.0
    turn = 0.0
    last_px = None
    sign = -1.0 if not flip else 1.0     # -1: contra deviatiei (mean-reversion)

    for t in range(n - 1):
        dev = (c[t] - vwap[t]) / vwap[t]
        raw = sign * dev / d_ref
        target_units = np.clip(np.round(raw / QUANT) * QUANT, -maxpos, maxpos)
        fill_idx = t + 1
        assert fill_idx > t                      # L1: fill dupa semnal
        if fill_idx == n - 1:
            target_units = 0.0                   # flat la open-ul ultimei bare
        px = o[fill_idx]
        # mark-to-market pana la fill
        if last_px is not None:
            mtm += shares * (px - last_px)
        target_shares = target_units * E / px
        d_sh = target_shares - shares
        if abs(d_sh) * px > 1.0:                 # ignora fill-uri sub $1 notional
            fee = abs(d_sh) * px * COST_SIDE
            cash -= d_sh * px + fee
            cost += fee
            turn += abs(d_sh) * px
            shares = target_shares
        assert abs(shares * px) <= maxpos * E * 1.001 + 1  # L1: limita pozitie
        last_px = px
    # ultima bara: pozitia e deja 0 (flat la open-ul ei)
    assert abs(shares) * last_px < maxpos * E * 0.005 + 1e-6, "nu e flat la final de zi"
    # L2: ledger vs MTM
    ledger_pnl = cash + shares * last_px         # shares=0 => cash pur
    assert abs((ledger_pnl + cost) - mtm) < 0.01, \
        f"contabilitate dubla esuata: ledger={ledger_pnl+cost:.4f} mtm={mtm:.4f}"
    mkt = c[n - 2] / o[0] - 1.0                  # randament intraday (fereastra activa)
    return ledger_pnl, cost, turn, mkt


def run(df, d_ref, maxpos, flip=False):
    days = []
    for d, dd in df.groupby("day"):
        r = daily_sim(dd.reset_index(drop=True), d_ref, maxpos, flip)
        if r is not None:
            days.append(dict(day=d, pnl=r[0], cost=r[1], turn=r[2], mkt=r[3]))
    t = pd.DataFrame(days)
    return t


def stats(t, label):
    r = t["pnl"].values / E                      # randament zilnic (fractie din E)
    m = t["mkt"].values
    nd = len(r)
    tstat = r.mean() / (r.std(ddof=1) / np.sqrt(nd)) if r.std() > 0 else 0
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    # alpha/beta via OLS
    X = np.vstack([np.ones(nd), m]).T
    beta_v = np.linalg.lstsq(X, r, rcond=None)[0]
    resid = r - X @ beta_v
    se_a = np.sqrt(resid.var(ddof=2) * (np.linalg.inv(X.T @ X)[0, 0]))
    t_alpha = beta_v[0] / se_a if se_a > 0 else 0
    cum = np.cumsum(r)
    dd = (cum - np.maximum.accumulate(cum)).min() * 100
    print(f"  {label:34s} zile={nd:4d} pnl/zi={r.mean()*1e4:+6.2f}bps t={tstat:+5.2f} "
          f"Sharpe={sharpe:+5.2f} alpha/zi={beta_v[0]*1e4:+6.2f}bps t_a={t_alpha:+5.2f} "
          f"beta={beta_v[1]:+.2f} maxDD={dd:5.1f}% cost/zi={t['cost'].mean():.0f}$ "
          f"turn/zi={t['turn'].mean()/E:.1f}xE")
    return dict(t=tstat, t_alpha=t_alpha, mean=r.mean())


if __name__ == "__main__":
    df = load_mt5([f"NDX100_M5 {n}.csv" for n in range(1, 7)])
    print(f"DEV 2019-2024: {df['day'].nunique()} zile")
    print("\nGRID (d_ref = deviatia pt 1 unitate; maxpos = plafon unitati):")
    results = {}
    for d_ref in [0.001, 0.002, 0.004]:
        for maxpos in [1.0, 2.0, 4.0]:
            t = run(df, d_ref, maxpos)
            results[(d_ref, maxpos)] = stats(t, f"dref={d_ref*100:.1f}% maxpos={maxpos:.0f}")
    print("\nCONTROL semn inversat (momentum, dref=0.2% maxpos=2):")
    stats(run(df, 0.002, 2.0, flip=True), "FLIP momentum")
    print("\nCONTROL fara costuri (dref=0.2% maxpos=2) — edge brut:")
    COST0 = COST_SIDE
    import vwap_test as _self  # noqa
    globals()["COST_SIDE"] = 0.0
    stats(run(df, 0.002, 2.0), "fara costuri")
    globals()["COST_SIDE"] = COST0

    elig = [(k, v) for k, v in results.items() if v["mean"] > 0 and v["t"] >= 2 and v["t_alpha"] >= 2]
    print("\n" + "=" * 90)
    if not elig:
        print("REGULA PRE-INREGISTRATA: nicio configuratie cu t>=2 SI t_alpha>=2 -> holdout NEATINS.")
    else:
        print(f"Eligibile pt holdout: {[k for k, _ in elig]}")
