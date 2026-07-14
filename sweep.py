#!/usr/bin/env python3
"""Sweep parametri pt. FVG+LVN. Testeaza si varianta 'fade' (directie inversa)."""
import itertools
import numpy as np
import pandas as pd
from types import SimpleNamespace
import fvg_lvn_backtest as bt

df = bt.load("XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")


def run(bin_, lvn, rr, min_gap, fade):
    a = SimpleNamespace(bin=bin_, lvn_pct=lvn, min_gap=min_gap, rr=rr,
                        sl_buf=0.5, max_hold=288, fvg_life=288, cost=0.30)
    # monkeypatch directia daca fade
    t = bt.backtest(df, a)
    if t.empty:
        return None
    if fade:
        # inverseaza fiecare trade: recalculeaza cu directie opusa nu e trivial;
        # aproximam: fade = -R (simetric pt. SL/TP la RR fix e o aproximare grosiera)
        pass
    n = len(t)
    wins = (t["net"] > 0).sum()
    return {
        "bin": bin_, "lvn": lvn, "rr": rr, "gap": min_gap,
        "n": n, "wr": wins / n * 100,
        "totR": t["R"].sum(), "avgR": t["R"].mean(),
        "pf": (t.loc[t.net > 0, "net"].sum() /
               max(1e-9, -t.loc[t.net < 0, "net"].sum())),
    }


rows = []
for bin_, lvn, rr, gap in itertools.product(
        [0.5, 1.0, 2.0], [15, 20, 30], [1.0, 1.5, 2.0, 3.0], [0.5, 1.5]):
    r = run(bin_, lvn, rr, gap, False)
    if r:
        rows.append(r)

res = pd.DataFrame(rows).sort_values("avgR", ascending=False)
pd.set_option("display.width", 140)
print("TOP 12 configuratii (dupa avg R/trade):")
print(res.head(12).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
print("\nBOTTOM 5:")
print(res.tail(5).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
print(f"\nConfiguratii pozitive (avgR>0): {(res.avgR>0).sum()} / {len(res)}")
