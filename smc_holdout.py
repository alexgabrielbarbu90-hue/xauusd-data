#!/usr/bin/env python3
"""
Validare finala SMC pe HOLDOUT (nov2024-mai2026) — O SINGURA rulare.

Configuratii PRE-INREGISTRATE (regula anuntata inainte de a vedea holdout-ul:
top pe dev dupa avgR cu n>=200, plus combinatia ceruta explicit sweep_PD+OB
raportata cu avertisment de esantion mic):
  1. sweep_PD       toate  RR2.5   (dev: n=583, avgR=+0.020)
  2. sweep_PD_deep  toate  RR2.5   (dev: n=229, avgR=+0.049)
  3. sweep_PD+OB    toate  RR2.5   (dev: n=66,  avgR=+0.141)  [n mic!]

Pentru fiecare: t-stat pe dev, holdout la cost baza si la cost stress
(0.015% + slippage SL 0.010%).
"""
import numpy as np
import pandas as pd
from smc_test import (load_mt5, prep, sweeps_pd, ob_pass, simulate, met,
                      COST_BASE, COST_STRESS, SLIP_STRESS)


def tstat(t):
    R = t["R"].values
    return R.mean() / (R.std(ddof=1) / np.sqrt(len(R))) if len(R) > 2 and R.std() > 0 else 0.0


def line(label, t):
    m = met(t)
    print(f"  {label:44s} n={m['n']:4d} WR={m['wr']:5.1f}% avgR={m['avgR']:+.3f} "
          f"PF={m['pf']:4.2f} t-stat={tstat(t):+.2f}")
    return t


dev = prep(load_mt5([f"NDX100_M5 {n}.csv" for n in range(1, 7)]))
hold = prep(load_mt5(["NDX100_M5_202411010100_202605042355.csv"]))
print("Semnale dev + holdout...")
ib_d, ie_d, _ = ob_pass(dev)
ib_h, ie_h, _ = ob_pass(hold)

def sigs(ds, ib, ie):
    pd_ = sweeps_pd(ds)
    return {
        "sweep_PD rr2.5": (pd_, 2.5),
        "sweep_PD_deep rr2.5": (sweeps_pd(ds, depth=0.0005), 2.5),
        "sweep_PD+OB rr2.5": ([s for s in pd_ if (s[1] == 1 and ib[s[0]])
                               or (s[1] == -1 and ie[s[0]])], 2.5),
    }

SD, SH = sigs(dev, ib_d, ie_d), sigs(hold, ib_h, ie_h)

for name in SD:
    sig_d, rr = SD[name]
    sig_h, _ = SH[name]
    print(f"\n=== {name} ===")
    line("DEV 2019-2024 (referinta selectiei)", simulate(dev, sig_d, rr, None))
    th = line("HOLDOUT cost baza (0.010%)", simulate(hold, sig_h, rr, None))
    line("HOLDOUT stress (0.015% + slip 0.010%)",
         simulate(hold, sig_h, rr, None, cost_pct=COST_STRESS, slip_pct=SLIP_STRESS))
    if not th.empty:
        th = th.copy()
        th["yr"] = pd.to_datetime(th["dt"]).dt.year
        ys = {yr: f"n={len(g)},avgR={g.R.mean():+.2f}" for yr, g in th.groupby("yr")}
        print(f"  holdout pe ani: {ys}")
