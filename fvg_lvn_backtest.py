#!/usr/bin/env python3
"""
FVG + Volume Profile (LVN) backtest pentru XAUUSD 5M.

Logica:
  - Volume Profile construit per SESIUNE (zi). LVN-urile pentru ziua D
    provin din profilul zilei D-1 (fara lookahead bias).
  - FVG detectat pe 5M (pattern 3 lumanari).
  - Confluenta: FVG-ul se suprapune cu un LVN al zilei precedente.
  - Intrare: pret retraseaza in zona FVG -> intri in DIRECTIA IMPULSULUI
    (respingere / continuare din LVN).
  - SL dincolo de marginea opusa a FVG; TP = R fix (configurabil).
"""
import argparse
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- parametri
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default="XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv")
    p.add_argument("--bin", type=float, default=1.0, help="marime bin VP ($)")
    p.add_argument("--lvn-pct", type=float, default=20.0, help="percentila sub care un bin e LVN")
    p.add_argument("--min-gap", type=float, default=0.5, help="marime minima FVG ($) pt. a filtra zgomot")
    p.add_argument("--rr", type=float, default=2.0, help="raport risk/reward (TP = rr * risc)")
    p.add_argument("--sl-buf", type=float, default=0.5, help="buffer SL dincolo de marginea FVG ($)")
    p.add_argument("--max-hold", type=int, default=288, help="max bare tinute (288 = ~1 zi)")
    p.add_argument("--fvg-life", type=int, default=288, help="cate bare ramane FVG-ul valid pt. intrare")
    p.add_argument("--cost", type=float, default=0.30, help="cost dus-intors (spread+comision) $/oz")
    p.add_argument("--fade", action="store_true", help="inverseaza directia (mizeaza pe umplere FVG)")
    return p.parse_args()


# ---------------------------------------------------------------- date
def load(csv):
    df = pd.read_csv(csv)
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    # "01.11.2024 00:00:00.000 GMT+0200"
    ts = df["time"].str.replace(r"\s*GMT[+-]\d+$", "", regex=True)
    df["dt"] = pd.to_datetime(ts, format="%d.%m.%Y %H:%M:%S.%f")
    df["date"] = df["dt"].dt.date
    # elimina barele moarte (piata inchisa): volum 0 si OHLC identice
    dead = (df["volume"] == 0) & (df["high"] == df["low"])
    df = df[~dead].reset_index(drop=True)
    return df


# ---------------------------------------------------------------- volume profile / LVN
def day_lvn_bins(day_df, bin_size, lvn_pct):
    """Returneaza setul de bin-uri LVN (index-uri de bin) pt. o zi."""
    lo = np.floor(day_df["low"].min() / bin_size) * bin_size
    hi = np.ceil(day_df["high"].max() / bin_size) * bin_size
    if hi <= lo:
        return set(), bin_size
    edges = np.arange(lo, hi + bin_size, bin_size)
    nb = len(edges) - 1
    vol = np.zeros(nb)
    # distribuie volumul fiecarei lumanari uniform pe bin-urile atinse [low, high]
    for l, h, v in zip(day_df["low"].values, day_df["high"].values, day_df["volume"].values):
        i0 = int((l - lo) // bin_size)
        i1 = int((h - lo) // bin_size)
        i0 = max(0, min(nb - 1, i0))
        i1 = max(0, min(nb - 1, i1))
        n = i1 - i0 + 1
        vol[i0:i1 + 1] += v / n
    touched = vol > 0
    if touched.sum() < 3:
        return set(), lo
    thresh = np.percentile(vol[touched], lvn_pct)
    lvn = set(i for i in range(nb) if touched[i] and vol[i] <= thresh)
    return lvn, lo


def price_in_lvn(price, lvn_bins, lo, bin_size):
    if not lvn_bins:
        return False
    idx = int((price - lo) // bin_size)
    return idx in lvn_bins


def zone_overlaps_lvn(z_bot, z_top, lvn_bins, lo, bin_size):
    """FVG [z_bot, z_top] se suprapune cu vreun bin LVN?"""
    if not lvn_bins:
        return False
    i0 = int((z_bot - lo) // bin_size)
    i1 = int((z_top - lo) // bin_size)
    for i in range(min(i0, i1), max(i0, i1) + 1):
        if i in lvn_bins:
            return True
    return False


# ---------------------------------------------------------------- FVG
def find_fvgs(df, min_gap):
    """Lista FVG: (idx_confirmare, dir, z_bot, z_top)."""
    o = df["high"].values
    hi = df["high"].values
    lo = df["low"].values
    out = []
    for i in range(2, len(df)):
        # bullish: high[i-2] < low[i]
        if hi[i - 2] < lo[i] and (lo[i] - hi[i - 2]) >= min_gap:
            out.append((i, 1, hi[i - 2], lo[i]))
        # bearish: low[i-2] > high[i]
        elif lo[i - 2] > hi[i] and (lo[i - 2] - hi[i]) >= min_gap:
            out.append((i, -1, hi[i], lo[i - 2]))
    return out


# ---------------------------------------------------------------- backtest
def backtest(df, a):
    dates = sorted(df["date"].unique())
    # profil LVN per zi
    lvn_by_day = {}
    for d in dates:
        dd = df[df["date"] == d]
        lvn_by_day[d] = day_lvn_bins(dd, a.bin, a.lvn_pct)

    prev = {dates[i]: dates[i - 1] for i in range(1, len(dates))}

    hi = df["high"].values
    lo = df["low"].values
    cl = df["close"].values
    dvec = df["date"].values
    n = len(df)

    fvgs = find_fvgs(df, a.min_gap)
    trades = []
    busy_until = -1  # o singura pozitie odata

    fade = getattr(a, "fade", False)
    for (i, imp_dir, z_bot, z_top) in fvgs:
        d = df["date"].iloc[i]
        if d not in prev:
            continue
        lvn_bins, lo_ref = lvn_by_day[prev[d]]
        if not zone_overlaps_lvn(z_bot, z_top, lvn_bins, lo_ref, a.bin):
            continue

        # asteapta retrasare in zona FVG (declansata de directia impulsului)
        entry_idx = None
        for j in range(i + 1, min(i + 1 + a.fvg_life, n)):
            if j <= busy_until:
                continue
            if imp_dir == 1 and lo[j] <= z_top:        # pullback in gap de sus
                entry_idx = j
                entry = min(z_top, hi[j])
                break
            if imp_dir == -1 and hi[j] >= z_bot:       # pullback in gap de jos
                entry_idx = j
                entry = max(z_bot, lo[j])
                break
        if entry_idx is None:
            continue

        # directia trade-ului: respingere = impuls; fade = invers (umplere FVG)
        direction = -imp_dir if fade else imp_dir

        # SL / TP
        if direction == 1:
            sl = z_bot - a.sl_buf
            risk = entry - sl
            tp = entry + a.rr * risk
        else:
            sl = z_top + a.sl_buf
            risk = sl - entry
            tp = entry - a.rr * risk
        if risk <= 0:
            continue

        # simuleaza forward
        outcome = None
        exit_px = None
        for k in range(entry_idx, min(entry_idx + a.max_hold, n)):
            if direction == 1:
                if lo[k] <= sl:
                    outcome, exit_px = "SL", sl
                    break
                if hi[k] >= tp:
                    outcome, exit_px = "TP", tp
                    break
            else:
                if hi[k] >= sl:
                    outcome, exit_px = "SL", sl
                    break
                if lo[k] <= tp:
                    outcome, exit_px = "TP", tp
                    break
        else:
            k = min(entry_idx + a.max_hold, n) - 1
            outcome, exit_px = "TIME", cl[k]

        gross = (exit_px - entry) * direction
        net = gross - a.cost
        r_mult = net / risk
        trades.append({
            "date": d, "dir": direction, "entry": entry, "sl": sl, "tp": tp,
            "exit": exit_px, "outcome": outcome, "risk": risk,
            "gross": gross, "net": net, "R": r_mult,
        })
        busy_until = k

    return pd.DataFrame(trades)


# ---------------------------------------------------------------- raport
def report(t, a):
    if t.empty:
        print("Niciun trade generat.")
        return
    n = len(t)
    wins = (t["net"] > 0).sum()
    wr = wins / n * 100
    tot_R = t["R"].sum()
    avg_R = t["R"].mean()
    gp = t.loc[t["net"] > 0, "net"].sum()
    gl = -t.loc[t["net"] < 0, "net"].sum()
    pf = gp / gl if gl > 0 else float("inf")
    eq = t["R"].cumsum()
    dd = (eq - eq.cummax()).min()
    by = t["outcome"].value_counts().to_dict()

    print("=" * 56)
    print(" FVG + LVN (VP zilnic, respingere/continuare) — XAUUSD 5M")
    print("=" * 56)
    print(f"  Parametri: bin=${a.bin}  LVN<{a.lvn_pct}%  minGap=${a.min_gap}  "
          f"RR={a.rr}  cost=${a.cost}")
    print("-" * 56)
    print(f"  Trades totale      : {n}")
    print(f"  Win rate           : {wr:.1f}%  ({wins}W / {n-wins}L)")
    print(f"  Rezultate          : {by}")
    print(f"  Total R            : {tot_R:+.1f} R")
    print(f"  Avg R / trade      : {avg_R:+.3f} R")
    print(f"  Profit factor      : {pf:.2f}")
    print(f"  Max drawdown       : {dd:.1f} R")
    print(f"  Net $ (1oz/trade)  : {t['net'].sum():+.1f}")
    print(f"  Long / Short       : {(t['dir']==1).sum()} / {(t['dir']==-1).sum()}")
    print("=" * 56)


if __name__ == "__main__":
    a = parse_args()
    df = load(a.csv)
    print(f"Bare valide: {len(df):,}  |  Zile: {df['date'].nunique()}  "
          f"|  Interval pret: {df['low'].min():.0f}-{df['high'].max():.0f}\n")
    t = backtest(df, a)
    report(t, a)
    t.to_csv("fvg_lvn_trades.csv", index=False)
    print("\nDetalii -> fvg_lvn_trades.csv")
