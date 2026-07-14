#!/usr/bin/env python3
"""
AUDIT LIVE-READINESS (MT5) pentru strategia FVG+LVN.

Masoara, in ordine:
  A. Integritatea datelor (timezone/DST, gauri, spread real, alinierea sesiunii).
  B. Bug-uri de simulare si impactul lor:
       - ordinea ne-cronologica a trade-urilor (motor original vs event-driven)
       - TP optimist pe bara de intrare
       - re-touch pe zone "consumate" vs first-touch strict
  C. Costuri de executie reale: spread per-bara din coloana SPREAD, slippage pe SL,
     exit fortat end-of-day (elimina swap/weekend).
  D. Fezabilitate marja/levier la risc 1% cu SL strans + drag estimat de swap.
  E. HOLDOUT adevarat: NDX100_M5_202411010100_202605042355.csv (nov 2024 - mai 2026),
     date nefolosite la nicio optimizare.

Config INGHETAT (identic cu ndx_test.py): RR=1.5, LVN<25%, ore 15-19, N_BINS=23,
gap/SLbuf/cost ca % derivate din referinta aur.
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import fvg_lvn_backtest as bt

RR, LVN_PCT, H0, H1 = 1.5, 25, 15, 19
FRESH = MAXH = 288
GOLD_CSV = "XAUUSD_Candlestick_5_M_BID_01.11.2024-01.11.2025.csv"

# ---------- parametri % din referinta aur (formulele exacte din ndx_test.py)
gold = bt.load(GOLD_CSV)
gref = gold["close"].median()
GAP_PCT, SLBUF_PCT, COST_PCT = 1.5 / gref, 0.25 / gref, 0.30 / gref
gdr = gold.groupby("date").apply(lambda x: x["high"].max() - x["low"].min(),
                                 include_groups=False).median()
N_BINS = max(15, int(round(gdr / 2.0)))

# ============================================================ A. DATA INTEGRITY
print("=" * 96)
print(" A. INTEGRITATEA DATELOR")
print("=" * 96)
raw = open(GOLD_CSV).read()
print(f"Aur: randuri GMT+0200={raw.count('GMT+0200'):,}  GMT+0300={raw.count('GMT+0300'):,}"
      f"  -> {'timezone MIXT (EET cu DST) - orele urmaresc DST' if raw.count('GMT+0300') else 'offset fix'}")
del raw


def load_mt5(files):
    parts = []
    for f in files:
        d = pd.read_csv(f, sep="\t")
        d.columns = [c.strip("<>").lower() for c in d.columns]
        d["dt"] = pd.to_datetime(d["date"] + " " + d["time"], format="%Y.%m.%d %H:%M:%S")
        parts.append(d)
    m = pd.concat(parts).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    m["volume"] = m["tickvol"].astype(float)
    m["day"] = m["dt"].dt.date
    return m


def integrity(df, label):
    bpd = df.groupby("day").size()
    wd = sorted(set(pd.to_datetime(pd.Series(list(df["day"].unique()))).dt.dayofweek))
    gaps = pd.Series(sorted(df["day"].unique()))
    gd = (pd.to_datetime(gaps).diff().dt.days > 3).sum()
    hh = df.groupby(df["dt"].dt.hour)["volume"].mean()
    mon = df["dt"].dt.month
    win = df[mon.isin([11, 12, 1, 2])].groupby(df["dt"].dt.hour)["volume"].mean()
    sm = df[mon.isin([6, 7, 8])].groupby(df["dt"].dt.hour)["volume"].mean()
    inwin = df[(df["dt"].dt.hour >= H0) & (df["dt"].dt.hour <= H1)]
    print(f"\n{label}: {len(df):,} bare | {df['dt'].min()} -> {df['dt'].max()} | zile={df['day'].nunique()}")
    print(f"  bare/zi median={int(bpd.median())} p5={int(bpd.quantile(.05))} | weekday-uri={wd} "
          f"| tickvol=0: {(df['volume']==0).sum()} | gauri>3zile: {gd}")
    print(f"  ora varf volum: total={hh.idxmax()} iarna={win.idxmax()} vara={sm.idxmax()} "
          f"| %volum in 15-19: {inwin['volume'].sum()/df['volume'].sum()*100:.0f}%")
    print(f"  SPREAD (puncte): median={df['spread'].median():.0f} p95={df['spread'].quantile(.95):.0f}"
          f" | in fereastra 15-19: median={inwin['spread'].median():.0f} p95={inwin['spread'].quantile(.95):.0f}"
      f" (1 punct=0.01 idx pt)")


nd6 = load_mt5([f"NDX100_M5 {n}.csv" for n in range(1, 7)])
hold = load_mt5(["NDX100_M5_202411010100_202605042355.csv"])
integrity(nd6, "NDX 2019-2024 (train instrument)")
integrity(hold, "NDX HOLDOUT nov2024-mai2026 (NEFOLOSIT pana acum)")
print(f"\nSuprapunere 6y vs holdout: 6y se termina {nd6['dt'].max().date()}, "
      f"holdout incepe {hold['dt'].min().date()} -> disjuncte (gaura oct 2024 = gol la furnizor)")
print(f"Cost presupus pana acum: {COST_PCT*100:.4f}% din pret "
      f"(~{COST_PCT*nd6['close'].median():.2f} pt idx) vs spread real median "
      f"{nd6['spread'].median()*0.01:.2f} pt idx")


# ============================================================ pregatire comuna
def day_profile(dd):
    lo_, hi_ = dd["low"].min(), dd["high"].max()
    if hi_ <= lo_:
        return None
    w = (hi_ - lo_) / N_BINS
    vol = np.zeros(N_BINS)
    for l, h, v in zip(dd["low"].values, dd["high"].values, dd["volume"].values):
        i0 = min(N_BINS - 1, max(0, int((l - lo_) / w)))
        i1 = min(N_BINS - 1, max(0, int((h - lo_) / w)))
        vol[i0:i1 + 1] += v / (i1 - i0 + 1)
    t = vol > 0
    if t.sum() < 3:
        return None
    thr = np.percentile(vol[t], LVN_PCT)
    return set(i for i in range(N_BINS) if t[i] and vol[i] <= thr), lo_, hi_, w


def prep(df):
    ds = dict(hi=df["high"].values, lo=df["low"].values, cl=df["close"].values,
              hour=df["dt"].dt.hour.values, spread=df["spread"].values.astype(float),
              day=df["day"].values, dt=df["dt"].values, N=len(df))
    days = sorted(df["day"].unique())
    prof = {d: day_profile(dd) for d, dd in df.groupby("day")}
    prev = {days[i]: days[i - 1] for i in range(1, len(days))}
    tail = df.groupby("day").tail(1)
    ds["day_end"] = dict(zip(tail["day"], tail.index))
    fv = []
    for (i, imp, zb, zt) in bt.find_fvgs(df, 0.0):
        if (zt - zb) < GAP_PCT * ds["cl"][i]:
            continue
        d = ds["day"][i]
        if d not in prev:
            continue
        p = prof.get(prev[d])
        if p is None:
            continue
        lvn, plo, phi, w = p
        if zt < plo or zb > phi:
            continue
        i0 = max(0, int((zb - plo) / w))
        i1 = min(N_BINS - 1, int((zt - plo) / w))
        if not any(k in lvn for k in range(min(i0, i1), max(i0, i1) + 1)):
            continue
        fv.append((i, imp, zb, zt))
    ds["fv"] = fv
    return ds


# ---------- motorul ORIGINAL (portat fidel: proceseaza FVG in ordinea confirmarii)
# abs_risk=True replica EXACT semantica v2/ndx_test (risk=abs(entry-sl)) -> permite
# trade-uri degenerate cu intrarea DINCOLO de SL (le marcam "fake").
def run_original(ds, use_col_spread=False, abs_risk=False):
    hi, lo, cl, hour = ds["hi"], ds["lo"], ds["cl"], ds["hour"]
    N = ds["N"]
    trades = []
    busy = -1
    for (i, imp, zb, zt) in ds["fv"]:
        eidx = None
        for j in range(i + 1, min(i + 1 + FRESH, N)):
            if j <= busy:
                continue
            if not (H0 <= hour[j] <= H1):
                continue
            if imp == 1 and lo[j] <= zt:
                eidx, entry = j, min(zt, hi[j]); break
            if imp == -1 and hi[j] >= zb:
                eidx, entry = j, max(zb, lo[j]); break
        if eidx is None:
            continue
        buf = SLBUF_PCT * entry
        sl = zb - buf if imp == 1 else zt + buf
        signed = (entry - sl) * imp
        fake = signed <= 0
        if abs_risk:
            risk = abs(entry - sl)
            if risk <= 0:
                continue
        else:
            risk = signed
            if risk <= 0:
                continue
        tp = entry + imp * RR * risk
        ex = None
        for k in range(eidx, min(eidx + MAXH, N)):
            if imp == 1:
                if lo[k] <= sl: ex = sl; break
                if hi[k] >= tp: ex = tp; break
            else:
                if hi[k] >= sl: ex = sl; break
                if lo[k] <= tp: ex = tp; break
        if ex is None:
            k = min(eidx + MAXH, N) - 1
            ex = cl[k]
        cost = ds["spread"][eidx] * 0.01 if use_col_spread else COST_PCT * entry
        trades.append(dict(dt=ds["dt"][eidx], day=ds["day"][eidx],
                           R=((ex - entry) * imp - cost) / risk,
                           slpct=risk / entry, nights=0, fake=fake))
        busy = k
    return pd.DataFrame(trades)


# ---------- motorul EVENT-DRIVEN (cronologic = ce ar face un EA live)
def run_event(ds, use_col_spread=True, entry_bar_tp=True, slip=0.0, eod=False,
              consume_on_touch=False):
    hi, lo, cl, hour, day = ds["hi"], ds["lo"], ds["cl"], ds["hour"], ds["day"]
    N = ds["N"]
    fv = ds["fv"]
    de = ds["day_end"]
    ptr, active, busy = 0, [], -1
    trades = []
    for j in range(N):
        while ptr < len(fv) and fv[ptr][0] < j:
            active.append(fv[ptr]); ptr += 1
        if active and j - active[0][0] > FRESH:
            active = [f for f in active if j - f[0] <= FRESH]
        if not active:
            continue
        touched = [f for f in active
                   if (f[1] == 1 and lo[j] <= f[3]) or (f[1] == -1 and hi[j] >= f[2])]
        if not touched:
            continue
        can = j > busy and (H0 <= hour[j] <= H1)
        traded = None
        if can:
            i, imp, zb, zt = touched[0]                    # cel mai vechi confirmat
            entry = min(zt, hi[j]) if imp == 1 else max(zb, lo[j])
            buf = SLBUF_PCT * entry
            sl = zb - buf if imp == 1 else zt + buf
            risk = (entry - sl) * imp
            traded = touched[0]
            if risk > 0:
                tp = entry + imp * RR * risk
                end = min(j + MAXH, N)
                if eod:
                    end = min(end, de[day[j]] + 1)
                ex = None
                k = j
                for k in range(j, end):
                    if imp == 1:
                        if lo[k] <= sl: ex = sl - slip; break
                        if (k > j or entry_bar_tp) and hi[k] >= tp: ex = tp; break
                    else:
                        if hi[k] >= sl: ex = sl + slip; break
                        if (k > j or entry_bar_tp) and lo[k] <= tp: ex = tp; break
                if ex is None:
                    k = end - 1
                    ex = cl[k]
                cost = ds["spread"][j] * 0.01 if use_col_spread else COST_PCT * entry
                trades.append(dict(dt=ds["dt"][j], day=day[j],
                                   R=((ex - entry) * imp - cost) / risk,
                                   slpct=risk / entry, nights=(day[k] - day[j]).days))
                busy = k
        if consume_on_touch:
            for f in touched:
                active.remove(f)
        elif traded is not None:
            active.remove(traded)
    return pd.DataFrame(trades)


def met(t):
    if t.empty:
        return dict(n=0, wr=0, avgR=0, pf=0, maxdd=0, totR=0)
    R = t.sort_values("dt")["R"].values
    eq = 100000.0
    eqs = []
    for r in R:
        eq += r * 0.01 * eq
        eqs.append(eq)
    eqs = np.array(eqs)
    peak = np.maximum.accumulate(eqs)
    gp, gl = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=len(R), wr=(R > 0).mean() * 100, avgR=R.mean(), totR=R.sum(),
                pf=gp / gl if gl > 0 else 99, maxdd=((eqs - peak) / peak).min() * 100)


def show(name, t):
    m = met(t)
    print(f"  {name:52s} n={m['n']:5d} WR={m['wr']:5.1f}% avgR={m['avgR']:+.3f} "
          f"PF={m['pf']:4.2f} maxDD={m['maxdd']:6.1f}%")
    return t


print("\n" + "=" * 96)
print(" B0. REPRODUCEREA REZULTATELOR RAPORTATE + DESCOMPUNEREA BUG-ULUI abs(risk)")
print("=" * 96)
ds6 = prep(nd6)
dsh_early = prep(hold)
print(f"FVG eligibile: 6y={len(ds6['fv']):,}  holdout={len(dsh_early['fv']):,}")
for lbl, ds_ in [("NDX 6y", ds6), ("HOLDOUT", dsh_early)]:
    tb = run_original(ds_, False, abs_risk=True)   # replica exacta ndx_test/v2
    fk = tb[tb["fake"]]
    ok = tb[~tb["fake"]]
    print(f"  {lbl}: mod v2/abs -> n={len(tb)} WR={(tb.R>0).mean()*100:.1f}% avgR={tb.R.mean():+.3f}"
          f"  || trade-uri FALSE (intrare dincolo de SL): {len(fk)} ({len(fk)/len(tb)*100:.1f}%)"
          f" cu avgR={fk.R.mean():+.3f}  || restul REAL: avgR={ok.R.mean():+.3f}")

# aur: reproducem configul "optimizat" (+188%) cu v2 si detectam fake-urile
import fvg_lvn_v2 as v2
pg = v2.defaults(rr=1.5, hours=True, h0=15, h1=19, bin=2, lvn_pct=25,
                 min_gap=1.5, sl_buf=0.25)
tg = v2.run(gold, pg)
fkg = tg[(tg["outcome"] == "SL") & (tg["net"] > 0)]      # SL cu profit = imposibil real
print(f"  AUR config optimizat (v2, raportat +188%): n={len(tg)} avgR={tg.R.mean():+.3f}"
      f"  || SL-cu-profit (FALSE): {len(fkg)} ({len(fkg)/len(tg)*100:.1f}%) avgR={fkg.R.mean():+.3f}"
      f"  || fara ele: avgR={tg.drop(fkg.index).R.mean():+.3f}")

print("\n" + "=" * 96)
print(" B+C. IMPACTUL BUG-URILOR SI AL EXECUTIEI REALE  (NDX 2019-2024, config inghetat)")
print("=" * 96)
show("A0 motor corectat, ordinea veche (risc semnat+skip)", run_original(ds6, False))
a1 = show("A1 CRONOLOGIC (bug ordine fixat), acelasi cost %", run_event(ds6, False, True, 0, False, False))
a2 = show("A2 = A1 + spread REAL per-bara (col SPREAD)", run_event(ds6, True, True, 0, False, False))
a3 = show("A3 = A2 + FARA TP pe bara intrarii (pesimist)", run_event(ds6, True, False, 0, False, False))
a4 = show("A4 = A3 + slippage 1.0 pt idx pe SL", run_event(ds6, True, False, 1.0, False, False))
a5 = show("A5 = A4 + exit fortat end-of-day (zero swap)", run_event(ds6, True, False, 1.0, True, False))
s1 = show("S  = A4 dar FIRST-TOUCH strict (zona moare la atins)", run_event(ds6, True, False, 1.0, False, True))

# ============================================================ D. marja / swap
print("\n" + "=" * 96)
print(" D. MARJA, LEVIER, SWAP (pe varianta realista A4)")
print("=" * 96)
lev = 0.01 / a4["slpct"]
nights = a4["nights"]
swapR = nights * 0.0192 * lev            # financing ~7%/an pe notional
print(f"  SL distanta: median={a4['slpct'].median()*100:.3f}% din pret | "
      f"levier necesar pt risc 1%: median={lev.median():.0f}x p95={lev.quantile(.95):.0f}x")
print(f"  % trade-uri care cer levier >20x (cap EU retail): {(lev>20).mean()*100:.0f}%  "
      f"| >100x: {(lev>100).mean()*100:.0f}%")
print(f"  Trade-uri peste noapte: {(nights>0).mean()*100:.1f}% | peste weekend: {(nights>=2).mean()*100:.1f}%")
print(f"  Drag SWAP estimat (7%/an financing): {swapR.mean():+.3f}R per trade "
      f"(avgR A4 {a4['R'].mean():+.3f} -> net ~{a4['R'].mean()-swapR.mean():+.3f})")
print(f"  Ore de intrare (A2): {pd.Series(pd.to_datetime(a2['dt'])).dt.hour.value_counts().sort_index().to_dict()}")

# ---- aur cu motorul CORECTAT (aceiasi parametri % ca NDX, ore 15-19)
print("\n  AUR cu motor corectat (event-driven, parametri % inghetati, cost $0.30):")
gold_df = gold.rename(columns={"date": "day"}).copy()
gold_df["spread"] = 0.0
dsg = prep(gold_df)
show("  G-A1 aur cronologic, cost fix", run_event(dsg, False, True, 0, False, False))
show("  G-A3 aur pesimist (fara TP bara 1)", run_event(dsg, False, False, 0, False, False))

# ============================================================ E. HOLDOUT
print("\n" + "=" * 96)
print(" E. HOLDOUT nov2024-mai2026 — date NEFOLOSITE la nicio decizie de pana acum")
print("=" * 96)
dsh = dsh_early
print(f"FVG eligibile: {len(dsh['fv']):,}")
show("H-A0 motor original, cost % (comparabil cu 6y)", run_original(dsh, False))
h2 = show("H-A2 cronologic + spread real", run_event(dsh, True, True, 0, False, False))
h4 = show("H-A4 realist-pesimist (fara TP bara 1, slip 1pt)", run_event(dsh, True, False, 1.0, False, False))
h5 = show("H-A5 = H-A4 + exit end-of-day", run_event(dsh, True, False, 1.0, True, False))

print("\n  H-A4 pe ani (regimuri nevazute):")
h4y = h4.copy()
h4y["yr"] = pd.to_datetime(h4y["dt"]).dt.year
for yr in sorted(h4y["yr"].unique()):
    m = met(h4y[h4y.yr == yr])
    print(f"    {yr}: n={m['n']:4d} WR={m['wr']:5.1f}% avgR={m['avgR']:+.3f} PF={m['pf']:4.2f}")

# ---------- grafic holdout (risc fix, fara compunere = vederea onesta)
def fixed_eq(t):
    t = t.sort_values("dt").reset_index(drop=True)
    return pd.DataFrame({"dt": pd.to_datetime(t["dt"]),
                         "eq": 100000 + (t["R"] * 1000).cumsum()})

plt.figure(figsize=(12, 5.5))
for nm, t, c in [("H-A2 spread real", h2, "#1f77b4"),
                 ("H-A4 pesimist (slip+fara TP bara1)", h4, "#d62728"),
                 ("H-A5 pesimist + doar intraday", h5, "#2ca02c")]:
    if not t.empty:
        e = fixed_eq(t)
        plt.plot(e["dt"], e["eq"], label=nm, lw=1.7, color=c)
plt.axhline(100000, color="black", ls="--", lw=0.8, alpha=0.5)
plt.title("HOLDOUT NDX nov2024-mai2026 (date nevazute) — risc fix $1k/trade, motor cronologic",
          fontsize=12, fontweight="bold")
plt.ylabel("Equity ($)"); plt.xlabel("Data"); plt.legend(); plt.grid(alpha=0.25)
plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
plt.gcf().autofmt_xdate()
plt.tight_layout()
plt.savefig("audit_holdout.png", dpi=130, bbox_inches="tight")
print("\nGrafic -> audit_holdout.png")
