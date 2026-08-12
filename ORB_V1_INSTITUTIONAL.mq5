//+------------------------------------------------------------------+
//| ORB_V1_INSTITUTIONAL.mq5                                         |
//|                                                                  |
//| ORB V1 INSTITUTIONAL — evolutia V6 ULTIMATE pe baza programului  |
//| de research V6-NEXT (793 configs, walk-forward 2022-2026,        |
//| clean-room verified). NU taie trade-uri: semnalele devin         |
//| MODULATORI DE RISC, nu skip-filters.                             |
//|                                                                  |
//| MODIFICARI vs ORB_V6_ULTIMATE (toate validate WF OOS):           |
//| [INST-1] ANTI-CHASE risk mod: daca bara de semnal are            |
//|          TrueRange > 1.5 x ATR14(M5 intraday, cauzal),           |
//|          riscul se inmulteste cu 0.40 (nu skip!).                |
//|          OOS: zilele chase au exp +0.026R vs +0.143R normal.     |
//| [INST-2] RSI TIER BOOST: RSI14(M5) la ultima bara OR >=75 (L)    |
//|          / <=25 (S), exceptand lunea -> risc x1.50.              |
//|          OOS: aceste zile au exp +0.62R, WR 75% (n=40).          |
//| [INST-3] TP1 mutat 1.5R -> 2.0R (50% partial), TP2 OFF,          |
//|          CAP 2.0R (= full exit la 2R; paired-t +2.47).           |
//| [INST-4] MARTINGALE WEEK-RESET: lantul x1.5 se reseteaza si      |
//|          la saptamana NY noua (taie coada de risc).              |
//| [INST-5] Tiers default = valorile LIVE validate:                 |
//|          T1=.10 T2=.15 T3=.20 T4=.25 T5=.40 T6=.55 Cap=1.5       |
//| Validare: exp/risc OOS +0.148R (vs +0.085 baseline), Sharpe_d    |
//| 2.13, 9/9 ferestre pozitive, 905/905 trade-uri pastrate.         |
//|                                                                  |
//| [MS] MULTI-SYMBOL (v1.10):                                       |
//|  - Problema: scoring-ul folosea praguri ABSOLUTE in puncte de    |
//|    pret (MinOR=35, brackets OR 35/50/70/100/150) si praguri      |
//|    absolute de vol anualizata (15/20/25/30) -> valabile DOAR pe  |
//|    simbolul pe care au fost calibrate (Nasdaq). Pe alt simbol,   |
//|    scara de pret difera si scorul devine invalid.                |
//|  - Solutie: InpScoreMode = SCORE_ATR_NORM:                       |
//|    [MS-1] OR range scorat ca FRACTIE din ATR D1 (adimensional)   |
//|           + MinOR relativ la ATR; RVol scorat ca PERCENTILA      |
//|           istorica (fereastra InpRVolPercDays), nu valoare       |
//|           absoluta. Body/UW/ClosePos erau deja adimensionale.    |
//|    [MS-2] fallback SL preview generic (~0.5% din pret) in loc    |
//|           de 150 puncte hardcodate                               |
//|    [MS-3] InpPortfolioScale: imparte riscul per instanta cand    |
//|           rulezi pe N simboluri (ex: 0.5 la 2 simboluri)         |
//|  - Default = SCORE_LEGACY_ABS: comportament IDENTIC cu V1 pe     |
//|    Nasdaq. ATR_NORM se activeaza per chart pt alte simboluri.    |
//|  - Deploy multi-symbol: ataseaza EA pe cate UN CHART per simbol; |
//|    InpMagicPerSymbol=true separa deja magic-urile [FIX-6].       |
//|  - ATENTIE: pragurile INST/tiers sunt validate WF pe Nasdaq;     |
//|    pe alt simbol structura e transferabila dar re-valideaza      |
//|    inainte de live (backtest per simbol).                        |
//|                                                                  |
//| Mosteneste TOATE fix-urile si feature-urile V6 (FIX-1..8,        |
//| heartbeat, tier-lots panel, manual-close mart reset, vizuale).   |
//+------------------------------------------------------------------+
#property copyright "ORB V1 INSTITUTIONAL"
#property version   "1.10"
#property strict

#include <Trade\Trade.mqh>

enum ENUM_MOD_SCHEME
{
   MOD_AGGRESSIVE,
   MOD_SOFT,
   MOD_CONSERVATIVE,
   MOD_AMPLIFIER
};

// [FIX-2] Mode broker offset
enum ENUM_BROKER_MODE
{
   BROKER_AUTO_EU_DST,        // urmareste DST EU mereu (cel mai sigur)
   BROKER_TRANSITION_UTC3,    // pre-data: DST EU, post-data: UTC+3 fix
   BROKER_FIXED_UTC3          // UTC+3 fix tot timpul
};

// [MS] Mod scoring: praguri absolute (single-symbol) vs normalizate (multi-symbol)
enum ENUM_SCORE_MODE
{
   SCORE_LEGACY_ABS,          // praguri absolute in puncte (V1 original, Nasdaq)
   SCORE_ATR_NORM             // OR/ATR D1 + RVol percentila (transferabil pe orice simbol)
};

//--- Inputs
input group "=== OR FILTERS ==="
input double   InpMinOR         = 35.0;
input double   InpATRLo         = 0.15;
input double   InpATRHi         = 0.70;
input int      InpATRPeriod     = 20;

input group "=== SCORING / RISK TIERS ==="
input double   InpRiskT1        = 0.1;
input double   InpRiskT2        = 0.15;
input double   InpRiskT3        = 0.2;
input double   InpRiskT4        = 0.25;
input double   InpRiskT5        = 0.4;
input double   InpRiskT6        = 0.55;
input double   InpRiskCap       = 1.5;
input int      InpRVolPeriod    = 20;

input group "=== [MS] MULTI-SYMBOL SCORING ==="
// [MS-1] LEGACY_ABS = identic V1 (praguri puncte, doar simbolul calibrat).
// ATR_NORM = OR range scorat ca fractie din ATR D1 si RVol ca percentila
// istorica -> acelasi cod ruleaza corect pe orice simbol (XAUUSD, NAS100...).
input ENUM_SCORE_MODE InpScoreMode = SCORE_LEGACY_ABS;
input double   InpMinOR_ATR     = 0.15;   // min OR ca fractie din ATR D1 (doar ATR_NORM)
input double   InpORNorm_B1     = 0.175;  // praguri OR/ATR: [B1,B2)=+8
input double   InpORNorm_B2     = 0.25;   // [B2,B3]=+15 (sweet spot)
input double   InpORNorm_B3     = 0.35;   // (B3,B4]=+12
input double   InpORNorm_B4     = 0.50;   // (B4,B5]=+3
input double   InpORNorm_B5     = 0.75;   // >B5=+5
input int      InpRVolPercDays  = 120;    // fereastra istorica pt percentila RVol (zile)
// [MS-3] Scalare risc per instanta cand EA ruleaza pe mai multe simboluri
// simultan (riscul e % din ACELASI equity pe fiecare chart). Ex: 2 simboluri
// -> 0.50 pastreaza riscul agregat zilnic la nivelul unui singur simbol.
input double   InpPortfolioScale = 1.0;

input group "=== MARTINGALE ==="
input bool     InpUseMart           = true;
input double   InpMartMult          = 1.5;     // [INST-5] x1.5 validat (nu 1.667)
input int      InpMartLookbackDays  = 5;
input bool     InpMartOnNewDayClose = false;
input bool     InpMartWeekReset     = true;    // [INST-4] reset lant la saptamana NY noua
// [MART RESET TOGGLE] Cand true la OnInit, ignora pierderi vechi din history
// si porneste cu MART OFF chiar daca ultimul deal a fost loss.
// MART va re-activa NORMAL pe orice loss nou care apare dupa start.
// Util pentru o singura zi - dupa session, seteaza FALSE inapoi.
input bool     InpForceResetMart    = false;

input group "=== TRADE MANAGEMENT ==="
input double   InpTP1R          = 2.0;    // [INST-3] 1.5R -> 2.0R (paired-t +2.47)
input double   InpTP1Pct        = 50.0;
input double   InpTP2R          = 0.0;    // [INST-3] OFF (CAP 2R face full exit)
input double   InpTrailAct      = 0.75;
input double   InpTrailDist     = 0.75;
input double   InpCapR          = 2.0;
input double   InpSpreadMult    = 3.0;

input group "=== [INST] INSTITUTIONAL RISK MODS ==="
// [INST-1] ANTI-CHASE: bara de semnal cu TrueRange > InpChaseATRMax x ATR(M5,14
// bare precedente, medie simpla, cauzal) => riscul se inmulteste cu
// InpChaseRiskMult. NU se sare ziua - se reduce sizing-ul (validat WF OOS).
input bool     InpChaseEnable      = true;
input double   InpChaseATRMax      = 1.5;    // prag TR_semnal / ATR14
input int      InpChaseATRPeriod   = 14;     // bare M5 pt media TR
input double   InpChaseRiskMult    = 0.40;   // multiplicator risc pe zi chase
// [INST-2] RSI TIER BOOST: RSI14(M5) la ultima bara OR >= Hi (long) /
// <= Lo (short), si nu e luni => risc x InpRSIBoostMult (OOS: +0.62R, WR 75%).
input bool     InpRSIBoostEnable   = true;
input double   InpRSIBoostHi       = 75.0;
input double   InpRSIBoostLo       = 25.0;
input double   InpRSIBoostMult     = 1.50;
input bool     InpRSIBoostSkipMon  = true;   // lunea NU se aplica boost

input group "=== SESSION NY ==="
input int      InpORStartH      = 9;
input int      InpORStartM      = 30;
input int      InpOREndH        = 9;
input int      InpOREndM        = 45;
input int      InpEODH          = 16;
input int      InpEODM          = 55;

input group "=== BROKER / EXECUTIE ==="
input int      InpSlippage      = 100;
input ulong    InpMagic         = 20260111;   // [V1I] magic nou, separat de V6
input bool     InpMagicPerSymbol = true;     // [FIX-6] adauga hash simbol la magic
input int      InpMaxRetry      = 5;
input int      InpRetryDelayMs  = 200;

input group "=== BROKER OFFSET / DST ==="
input ENUM_BROKER_MODE InpBrokerMode = BROKER_AUTO_EU_DST;  // [FIX-2]
input string   InpBrokerUTC3Date = "2026.03.08";            // folosit doar la BROKER_TRANSITION_UTC3

input group "=== [LIVE GUARDS] ==="
input int      InpMaxSpreadPts      = 200;
input bool     InpRequireFreeMargin = true;
input bool     InpRequireTradingEnv = true;
input bool     InpCheckStopsLevel   = true;    // [FIX-1] verifica STOPS_LEVEL broker
input bool     InpCheckFreezeLevel  = true;    // [FIX-4] verifica FREEZE_LEVEL broker

input group "=== RISK MODULATION ==="
input bool             InpEnableRiskMod = true;
input ENUM_MOD_SCHEME  InpModScheme     = MOD_AGGRESSIVE;
input double   InpMultConfHigh     = 1.30;
input double   InpMultExtremeStrong= 1.25;
input double   InpMultFVGTrend     = 1.20;
input double   InpMultSweepLondon  = 1.15;
input double   InpMultScoreLow     = 0.60;
input double   InpMultMonday       = 0.70;
input double   InpMultMidNoMomentum= 0.75;
input double   InpMultTrendConflict= 0.80;
input double   InpMultMin          = 0.30;
input double   InpMultMax          = 1.60;

input group "=== VISUAL LINES — OR + TP/CAP ==="
input bool     InpShowLines       = true;
input color    InpColorORH        = clrDodgerBlue;
input color    InpColorORL        = clrDodgerBlue;
input color    InpColorTP1        = clrLimeGreen;
input color    InpColorTP2        = clrSeaGreen;
input color    InpColorCap        = clrOrange;
input int      InpLineWidthOR     = 2;
input int      InpLineWidthTP     = 1;

input group "=== VISUAL LINES — TRAILING ==="
input bool     InpShowTrailActLine    = true;          // linie statica unde se activeaza trail
input bool     InpShowTrailLiveLine   = true;          // linie dinamica care urmareste SL dupa activare
input color    InpColorTrailAct       = clrGold;       // culoare linie activare (dotted)
input color    InpColorTrailLive      = clrYellow;     // culoare linie dinamica (solid)
input int      InpLineWidthTrail      = 1;

input group "=== HEARTBEAT / STATUS DISPLAY ==="
input bool     InpShowHeartbeat       = true;       // afiseaza panou status pe chart
input int      InpHeartbeatLogSec     = 60;         // print status in Experts log la fiecare X sec (0=off)
input color    InpHB_ColorActive      = clrLime;    // culoare cand EA e activ + tradeaza
input color    InpHB_ColorWaiting     = clrSilver;  // culoare cand asteapta sesiune/trade
input color    InpHB_ColorInTrade     = clrAqua;    // culoare cand are pozitie deschisa
input color    InpHB_ColorError       = clrRed;     // culoare cand are eroare
input int      InpHB_FontSize         = 9;
input int      InpHB_PosX             = 10;         // pixel X din coltul ales
input int      InpHB_PosY             = 18;         // pixel Y din coltul ales
input ENUM_BASE_CORNER InpHB_Corner   = CORNER_LEFT_UPPER;

input group "=== LOT / TIER PREVIEW + MANUAL-CLOSE RESET ==="
input bool     InpShowTierLots      = true;    // afiseaza in panou lotul pe fiecare tier (AZI / A DOUA ZI)
input double   InpTierLotSLpts       = 0;       // SL puncte estimat pt calcul lot (0=auto: OR range / ATR)
input double   InpTierLotSLfromATR   = 0.30;    // daca auto si OR necunoscut: SL = ATR_D1 * acest factor
input bool     InpResetMartOnManual  = true;    // inchidere MANUALA (client/mobil/web) => Mart OFF ziua urmatoare

//--- Globals
CTrade         trade;
ulong          g_actualMagic;     // [FIX-6] magic real folosit
double         g_orHigh;
double         g_orLow;
double         g_orRange;
bool           g_orDone;
bool           g_traded;
string         g_lastDay;
double         g_riskPct;
double         g_baseRiskPct;
double         g_entryPrice;
double         g_stopLoss;
double         g_riskPoints;
double         g_trailBest;
bool           g_trailActive;
bool           g_isBuy;
int            g_atrHandle;
int            g_lastScore;
bool           g_lastWasLoss;
bool           g_hadPosition;
bool           g_tp1Done;
bool           g_tp2Done;
datetime       g_brokerUTC3Date;
datetime       g_lastCloseTime;
bool           g_lastCloseManual = false;   // [MANUAL RESET] ultimul close a fost manual (client/mobil/web)
bool           g_instChase = false;         // [INST-1] ziua curenta e flagata anti-chase
bool           g_instRSIBoost = false;      // [INST-2] ziua curenta are RSI boost

int            g_h_ema50  = INVALID_HANDLE;
int            g_h_ema200 = INVALID_HANDLE;
int            g_h_rsi    = INVALID_HANDLE;

bool   g_fvg_bull;
bool   g_fvg_bear;
bool   g_choch_bull;
bool   g_choch_bear;
double g_lon_high;
double g_lon_low;
bool   g_ema_align_bull;
bool   g_ema_align_bear;
double g_rsi_orb;
double g_close_loc;
bool   g_close_loc_ext_long;
bool   g_close_loc_ext_short;
double g_lastMultiplier;

int            g_totalTrades;
int            g_wins;
int            g_losses;
int            g_tier0, g_tier1, g_tier2, g_tier3, g_tier4, g_tier5;

// Heartbeat / status tracking
datetime       g_hb_lastLogPrint = 0;
datetime       g_hb_initTime     = 0;
string         g_hb_lastSessionStatus = "INIT";   // INIT, PRE_OR, IN_OR, OR_DONE_WAITING, IN_TRADE, EOD, OUTSIDE_SESSION
string         g_hb_lastReason       = "";        // ultimul motiv pentru skip/decizie
string         g_hb_LBL_NAME = "ORBV1I_HEARTBEAT_PANEL";

//+------------------------------------------------------------------+
//| LINII COSMETICE — helpers                                         |
//+------------------------------------------------------------------+
string LineName(string suffix) { return StringFormat("ORBV1I_%llu_%s", g_actualMagic, suffix); }

void DrawHLine(string suffix, double price, color clr, int width, ENUM_LINE_STYLE style, string label)
{
   if(!InpShowLines) return;
   string name = LineName(suffix);
   if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble (0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString (0, name, OBJPROP_TEXT, label);
   ObjectSetString (0, name, OBJPROP_TOOLTIP, label);
}

void DeleteLine(string suffix)
{
   string name = LineName(suffix);
   if(ObjectFind(0, name) >= 0) ObjectDelete(0, name);
}

void DrawORLines()
{
   if(!InpShowLines || g_orHigh <= 0 || g_orLow >= DBL_MAX) return;
   DrawHLine("OR_H", g_orHigh, InpColorORH, InpLineWidthOR, STYLE_SOLID, StringFormat("OR_High %.2f", g_orHigh));
   DrawHLine("OR_L", g_orLow,  InpColorORL, InpLineWidthOR, STYLE_SOLID, StringFormat("OR_Low %.2f", g_orLow));
}

void DrawTradeLines()
{
   if(!InpShowLines || g_riskPoints <= 0 || g_entryPrice <= 0) return;
   double tp1 = g_isBuy ? g_entryPrice + g_riskPoints * InpTP1R : g_entryPrice - g_riskPoints * InpTP1R;
   double tp2 = g_isBuy ? g_entryPrice + g_riskPoints * InpTP2R : g_entryPrice - g_riskPoints * InpTP2R;
   double cap = g_isBuy ? g_entryPrice + g_riskPoints * InpCapR : g_entryPrice - g_riskPoints * InpCapR;
   if(InpTP1R > 0) DrawHLine("TP1", tp1, InpColorTP1, InpLineWidthTP, STYLE_DOT, StringFormat("TP1 %.1fR @%.0f%% = %.2f", InpTP1R, InpTP1Pct, tp1));
   if(InpTP2R > 0) DrawHLine("TP2", tp2, InpColorTP2, InpLineWidthTP, STYLE_DASH, StringFormat("TP2 %.1fR (full) = %.2f", InpTP2R, tp2));
   if(InpCapR > 0) DrawHLine("CAP", cap, InpColorCap, InpLineWidthTP, STYLE_DASHDOTDOT, StringFormat("CapR %.1fR (hard) = %.2f", InpCapR, cap));

   // Trail Activation line (statica)
   if(InpShowTrailActLine && g_orRange > 0 && InpTrailAct > 0)
   {
      double trailActPrice = g_isBuy ? g_entryPrice + g_orRange * InpTrailAct
                                     : g_entryPrice - g_orRange * InpTrailAct;
      DrawHLine("TRAIL_ACT", trailActPrice, InpColorTrailAct, InpLineWidthTrail, STYLE_DOT,
               StringFormat("Trail Activation @%.2f (entry %s%.1f)",
                           trailActPrice, g_isBuy?"+":"-", g_orRange*InpTrailAct));
   }
}

// Linie dinamica care urmareste SL dupa activare trail
void UpdateTrailLiveLine()
{
   if(!InpShowLines || !InpShowTrailLiveLine) return;
   if(!g_trailActive) return;
   DrawHLine("TRAIL_SL_LIVE", g_stopLoss, InpColorTrailLive, InpLineWidthTrail, STYLE_SOLID,
            StringFormat("Trail SL Live @%.2f", g_stopLoss));
}

void DeleteTradeLines()
{
   DeleteLine("TP1"); DeleteLine("TP2"); DeleteLine("CAP");
   DeleteLine("TRAIL_ACT"); DeleteLine("TRAIL_SL_LIVE");
}
void DeleteORLines() { DeleteLine("OR_H"); DeleteLine("OR_L"); }
void DeleteAllLines() { DeleteORLines(); DeleteTradeLines(); }

//+------------------------------------------------------------------+
//| INIT                                                              |
//+------------------------------------------------------------------+
int OnInit()
{
   g_orHigh = 0.0; g_orLow = DBL_MAX; g_orRange = 0.0;
   g_orDone = false; g_traded = false;
   g_trailActive = false; g_lastWasLoss = false;
   g_hadPosition = false; g_tp1Done = false; g_tp2Done = false;
   g_lastScore = 0; g_totalTrades = 0; g_wins = 0; g_losses = 0;
   g_tier0=0; g_tier1=0; g_tier2=0; g_tier3=0; g_tier4=0; g_tier5=0;
   g_entryPrice = 0.0; g_stopLoss = 0.0; g_riskPoints = 0.0;
   g_trailBest = 0.0;
   g_riskPct = InpRiskT1; g_baseRiskPct = InpRiskT1;
   g_lastMultiplier = 1.0;
   g_lastCloseTime = 0;
   g_lastCloseManual = false;
   g_instChase = false; g_instRSIBoost = false;

   // [FIX-6] Magic per symbol
   g_actualMagic = InpMagic;
   if(InpMagicPerSymbol)
   {
      int hash = 0;
      int symLen = StringLen(_Symbol);
      for(int i = 0; i < MathMin(symLen, 4); i++)
         hash += StringGetCharacter(_Symbol, i);
      g_actualMagic = InpMagic + (ulong)hash;
      PrintFormat("[FIX-6] Magic per symbol: %llu (base=%llu, hash=%d, symbol=%s)",
                  g_actualMagic, InpMagic, hash, _Symbol);
   }

   g_brokerUTC3Date = StringToTime(InpBrokerUTC3Date);
   if(g_brokerUTC3Date == 0 && InpBrokerMode == BROKER_TRANSITION_UTC3)
   { PrintFormat("INIT FAIL: Data tranzitie invalida: %s", InpBrokerUTC3Date); return(INIT_FAILED); }

   if(InpTP2R > 0.0 && InpTP1R > 0.0 && InpTP2R <= InpTP1R) { Print("INIT FAIL: TP2R<=TP1R"); return(INIT_FAILED); }
   // [INST-3] egalitate permisa: TP1R==CapR => partial la TP1 + restul la CAP acelasi nivel (full exit la 2R)
   if(InpCapR > 0.0 && InpTP1R > 0.0 && InpCapR < InpTP1R) { Print("INIT FAIL: CapR<TP1R"); return(INIT_FAILED); }
   if(InpMartLookbackDays < 1 || InpMartLookbackDays > 30) { Print("INIT FAIL: MartLookback in [1..30]"); return(INIT_FAILED); }

   // [MS] validari mod multi-symbol
   if(InpScoreMode == SCORE_ATR_NORM)
   {
      if(InpMinOR_ATR <= 0) { Print("INIT FAIL: MinOR_ATR<=0"); return(INIT_FAILED); }
      if(!(InpORNorm_B1 < InpORNorm_B2 && InpORNorm_B2 < InpORNorm_B3 &&
           InpORNorm_B3 < InpORNorm_B4 && InpORNorm_B4 < InpORNorm_B5))
      { Print("INIT FAIL: ORNorm B1<B2<B3<B4<B5 obligatoriu"); return(INIT_FAILED); }
      if(InpRVolPercDays < 30) { Print("INIT FAIL: RVolPercDays>=30"); return(INIT_FAILED); }
   }
   if(InpPortfolioScale <= 0) { Print("INIT FAIL: PortfolioScale<=0"); return(INIT_FAILED); }

   if(!SymbolSelect(_Symbol, true)) { PrintFormat("INIT FAIL: SymbolSelect code=%d", GetLastError()); return(INIT_FAILED); }

   trade.SetExpertMagicNumber(g_actualMagic);
   trade.SetDeviationInPoints(InpSlippage);

   // [FIX-5] Filling mode cu BOC fallback
   ENUM_ORDER_TYPE_FILLING detectedFill = ORDER_FILLING_RETURN;
   long fillModes = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fillModes & SYMBOL_FILLING_FOK) != 0)      detectedFill = ORDER_FILLING_FOK;
   else if((fillModes & SYMBOL_FILLING_IOC) != 0) detectedFill = ORDER_FILLING_IOC;
   else if((fillModes & SYMBOL_FILLING_BOC) != 0) detectedFill = ORDER_FILLING_BOC;
   else PrintFormat("INIT WARN [FIX-5]: no FOK/IOC/BOC, mask=%d - folosesc RETURN", fillModes);
   trade.SetTypeFilling(detectedFill);

   g_atrHandle = iATR(_Symbol, PERIOD_D1, InpATRPeriod);
   if(g_atrHandle == INVALID_HANDLE) { Print("INIT FAIL: iATR failed"); return(INIT_FAILED); }

   g_h_ema50  = iMA(_Symbol, PERIOD_D1, 50,  0, MODE_EMA, PRICE_CLOSE);
   g_h_ema200 = iMA(_Symbol, PERIOD_D1, 200, 0, MODE_EMA, PRICE_CLOSE);
   g_h_rsi    = iRSI(_Symbol, PERIOD_M5, 14, PRICE_CLOSE);
   if(g_h_ema50==INVALID_HANDLE || g_h_ema200==INVALID_HANDLE || g_h_rsi==INVALID_HANDLE)
      PrintFormat("INIT WARN: handles ema50=%d ema200=%d rsi=%d", g_h_ema50, g_h_ema200, g_h_rsi);

   g_lastDay = NYDateStr(TimeCurrent());
   DeleteAllLines();

   // [HEARTBEAT] init tracking
   g_hb_initTime = TimeCurrent();
   g_hb_lastLogPrint = 0;
   g_hb_lastReason = "INIT_OK";
   if(ObjectFind(0, g_hb_LBL_NAME)>=0) ObjectDelete(0, g_hb_LBL_NAME);

   if(!MQLInfoInteger(MQL_TESTER)) RecoverAll();

   PrintFormat("=== ORB V1 INSTITUTIONAL | %s | Magic=%llu ===", _Symbol, g_actualMagic);
   if(InpScoreMode == SCORE_ATR_NORM)
      PrintFormat("[MS] ScoreMode=ATR_NORM (multi-symbol) | MinOR=%.2fxATR | ORNorm=[%.3f/%.3f/%.3f/%.3f/%.3f] | RVolPerc=%dd | PortfolioScale=%.2f",
                  InpMinOR_ATR, InpORNorm_B1, InpORNorm_B2, InpORNorm_B3, InpORNorm_B4, InpORNorm_B5,
                  InpRVolPercDays, InpPortfolioScale);
   else
      PrintFormat("[MS] ScoreMode=LEGACY_ABS (single-symbol, praguri puncte) | PortfolioScale=%.2f", InpPortfolioScale);
   PrintFormat("[V1I INST] AntiChase=%s (max=%.2f x%.2f, ATR%d) | RSIBoost=%s (%g/%g x%.2f skipMon=%s) | MartWeekReset=%s",
               InpChaseEnable?"ON":"off", InpChaseATRMax, InpChaseRiskMult, InpChaseATRPeriod,
               InpRSIBoostEnable?"ON":"off", InpRSIBoostHi, InpRSIBoostLo, InpRSIBoostMult,
               InpRSIBoostSkipMon?"DA":"nu", InpMartWeekReset?"ON":"off");
   string schemeName = (InpModScheme==MOD_AGGRESSIVE)?"AGGRESSIVE":(InpModScheme==MOD_SOFT)?"SOFT":(InpModScheme==MOD_CONSERVATIVE)?"CONSERVATIVE":"AMPLIFIER";
   string brokerModeName = (InpBrokerMode==BROKER_AUTO_EU_DST)?"AUTO_EU_DST":(InpBrokerMode==BROKER_TRANSITION_UTC3)?"TRANSITION_UTC3":"FIXED_UTC3";
   PrintFormat("[V1I] Risk Mod: %s | Scheme: %s | Broker mode: %s",
               InpEnableRiskMod?"ACTIV":"OFF", schemeName, brokerModeName);
   PrintFormat("[V1I GUARDS] Spread=%d StopsLevel=%s FreezeLevel=%s FreeMargin=%s TradingEnv=%s Slippage=%d",
               InpMaxSpreadPts, InpCheckStopsLevel?"ON":"OFF", InpCheckFreezeLevel?"ON":"OFF",
               InpRequireFreeMargin?"ON":"OFF", InpRequireTradingEnv?"ON":"OFF", InpSlippage);
   PrintFormat("[V1I MARTINGALE] x%.3f | Lookback %d zile | NEW_DAY trigger Mart: %s",
               InpMartMult, InpMartLookbackDays, InpMartOnNewDayClose?"DA":"NU");
   PrintFormat("[V1I TIERS] T1=%.4f T2=%.3f T3=%.3f T4=%.3f T5=%.3f T6=%.3f | Cap=%.3f%%",
               InpRiskT1, InpRiskT2, InpRiskT3, InpRiskT4, InpRiskT5, InpRiskT6, InpRiskCap);
   PrintFormat("[V1I VISUAL] OR=ON | TP/CAP=%s | TrailAct=%s | TrailLive=%s",
               InpShowLines?"ON":"OFF",
               InpShowTrailActLine?"ON":"OFF",
               InpShowTrailLiveLine?"ON":"OFF");

   // Verificare specs broker
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freezeLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   PrintFormat("[V1I SYMBOL] StopsLevel=%d pts FreezeLevel=%d pts TickValue=%.4f TickSize=%.4f",
               (int)stopsLevel, (int)freezeLevel,
               SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE),
               SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE));

   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   if(g_atrHandle != INVALID_HANDLE) IndicatorRelease(g_atrHandle);
   if(g_h_ema50  != INVALID_HANDLE) IndicatorRelease(g_h_ema50);
   if(g_h_ema200 != INVALID_HANDLE) IndicatorRelease(g_h_ema200);
   if(g_h_rsi    != INVALID_HANDLE) IndicatorRelease(g_h_rsi);
   DeleteAllLines();
   // [HEARTBEAT] cleanup label
   if(ObjectFind(0, g_hb_LBL_NAME)>=0) ObjectDelete(0, g_hb_LBL_NAME);
   PrintFormat("=== STATS FINALE: Total=%d W=%d L=%d WR=%.1f%% ===",
               g_totalTrades, g_wins, g_losses,
               g_totalTrades > 0 ? (double)g_wins / g_totalTrades * 100.0 : 0.0);
}

//+------------------------------------------------------------------+
//| BROKER OFFSET / DST — [FIX-2] 3 moduri                            |
//+------------------------------------------------------------------+
int GetBrokerOffset(datetime serverTime)
{
   if(InpBrokerMode == BROKER_FIXED_UTC3) return(3);

   if(InpBrokerMode == BROKER_TRANSITION_UTC3)
   {
      if(serverTime >= g_brokerUTC3Date) return(3);
   }

   // BROKER_AUTO_EU_DST sau pre-tranzitie
   MqlDateTime s; TimeToStruct(serverTime, s);
   return IsEUDST(s.year, s.mon, s.day, s.hour) ? 3 : 2;
}

int SecondSundayMarch(int year)
{
   int cnt=0;
   for(int d=1; d<=14; d++)
   {
      MqlDateTime tmp; datetime dt = StringToTime(StringFormat("%d.%02d.%02d 12:00", year, 3, d));
      TimeToStruct(dt, tmp);
      if(tmp.day_of_week == 0) { cnt++; if(cnt==2) return d; }
   }
   return 14;
}
int FirstSundayNovember(int year)
{
   for(int d=1; d<=7; d++)
   {
      MqlDateTime tmp; datetime dt = StringToTime(StringFormat("%d.%02d.%02d 12:00", year, 11, d));
      TimeToStruct(dt, tmp);
      if(tmp.day_of_week == 0) return d;
   }
   return 1;
}
int LastSundayOfMonth(int year, int month)
{
   int last=1;
   for(int d=20; d<=31; d++)
   {
      MqlDateTime tmp; datetime dt = StringToTime(StringFormat("%d.%02d.%02d 12:00", year, month, d));
      TimeToStruct(dt, tmp);
      if(tmp.mon != month) break;
      if(tmp.day_of_week == 0) last = d;
   }
   return last;
}
bool IsUSDST(int year, int month, int day, int hour)
{
   if(month<3 || month>11) return false;
   if(month>3 && month<11) return true;
   if(month==3) { int sd=SecondSundayMarch(year); if(day>sd) return true; if(day==sd && hour>=7) return true; return false; }
   int fd=FirstSundayNovember(year);
   if(day<fd) return true;
   if(day==fd && hour<6) return true;
   return false;
}
bool IsEUDST(int year, int month, int day, int hour)
{
   if(month<3 || month>10) return false;
   if(month>3 && month<10) return true;
   if(month==3) { int ld=LastSundayOfMonth(year,3); if(day>ld) return true; if(day==ld && hour>=1) return true; return false; }
   int ld=LastSundayOfMonth(year,10);
   if(day<ld) return true;
   if(day==ld && hour<1) return true;
   return false;
}

datetime ServerToNY(datetime serverTime)
{
   MqlDateTime s; TimeToStruct(serverTime, s);
   int brokerOff = GetBrokerOffset(serverTime);
   int nyOff = IsUSDST(s.year, s.mon, s.day, s.hour) ? -4 : -5;
   return serverTime + (nyOff - brokerOff)*3600;
}
int NYMinOfDay(datetime serverTime) { MqlDateTime d; TimeToStruct(ServerToNY(serverTime), d); return d.hour*60 + d.min; }
string NYDateStr(datetime serverTime) { MqlDateTime d; TimeToStruct(ServerToNY(serverTime), d); return StringFormat("%d%02d%02d", d.year, d.mon, d.day); }
int NYDayOfWeek(datetime serverTime) { MqlDateTime d; TimeToStruct(ServerToNY(serverTime), d); return d.day_of_week; }

// [INST-4] index saptamana NY (saptamana incepe LUNI; 1970-01-01 = joi, +3 aliniaza)
long NYWeekIndex(datetime serverTime)
{
   long days = (long)(ServerToNY(serverTime) / 86400);
   return (days + 3) / 7;
}

//+------------------------------------------------------------------+
//| SCORING                                                            |
//+------------------------------------------------------------------+
int ComputeScore(double bodyPct, double uwPct, double orRng, double rVol, double cPos)
{
   int sc=0;
   if(bodyPct>=80) sc+=30; else if(bodyPct>=60) sc+=22; else if(bodyPct>=40) sc+=15; else if(bodyPct>=20) sc+=5;
   if(uwPct<=10) sc+=25; else if(uwPct<=20) sc+=18; else if(uwPct<=30) sc+=10; else if(uwPct<=50) sc+=5;
   if(orRng>=50 && orRng<=70) sc+=15; else if(orRng>70 && orRng<=100) sc+=12; else if(orRng>=35 && orRng<50) sc+=8;
   else if(orRng>150) sc+=5; else if(orRng>100 && orRng<=150) sc+=3;
   if(rVol>=15 && rVol<20) sc+=15; else if(rVol>=0 && rVol<15) sc+=12; else if(rVol>=20 && rVol<25) sc+=5; else if(rVol>=30) sc+=3;
   if(cPos>=80) sc+=15; else if(cPos<=20) sc+=8; else if(cPos>=60) sc+=7; else if(cPos<=40) sc+=3;
   if(sc>100) sc=100;
   return sc;
}

//+------------------------------------------------------------------+
//| [MS-1] SCORING NORMALIZAT — multi-symbol                          |
//| Aceeasi structura de punctaj ca ComputeScore, dar:                |
//|  - OR range: fractie din ATR D1 (adimensional) in loc de puncte  |
//|  - RVol: percentila vs. istoricul propriu al simbolului, in loc  |
//|    de praguri absolute de vol anualizata.                        |
//| Mapare percentila din legacy (15-20 best ~ mediana simbolului    |
//| calibrat): p30-60=+15, <p30=+12, p60-75=+5, p75-90=0, >=p90=+3.  |
//| Body/UW/ClosePos raman identice (deja adimensionale).            |
//+------------------------------------------------------------------+
int ComputeScoreNorm(double bodyPct, double uwPct, double orATRratio, double rvolPct, double cPos)
{
   int sc=0;
   if(bodyPct>=80) sc+=30; else if(bodyPct>=60) sc+=22; else if(bodyPct>=40) sc+=15; else if(bodyPct>=20) sc+=5;
   if(uwPct<=10) sc+=25; else if(uwPct<=20) sc+=18; else if(uwPct<=30) sc+=10; else if(uwPct<=50) sc+=5;
   double r = orATRratio;
   if(r>=InpORNorm_B2 && r<=InpORNorm_B3) sc+=15;
   else if(r>InpORNorm_B3 && r<=InpORNorm_B4) sc+=12;
   else if(r>=InpORNorm_B1 && r<InpORNorm_B2) sc+=8;
   else if(r>InpORNorm_B5) sc+=5;
   else if(r>InpORNorm_B4 && r<=InpORNorm_B5) sc+=3;
   if(rvolPct>=30 && rvolPct<60) sc+=15;
   else if(rvolPct<30) sc+=12;
   else if(rvolPct>=60 && rvolPct<75) sc+=5;
   else if(rvolPct>=90) sc+=3;
   if(cPos>=80) sc+=15; else if(cPos<=20) sc+=8; else if(cPos>=60) sc+=7; else if(cPos<=40) sc+=3;
   if(sc>100) sc=100;
   return sc;
}

double ScoreToRisk(int score)
{
   if(score>=85) return InpRiskT6;
   if(score>=68) return InpRiskT5;
   if(score>=51) return InpRiskT4;
   if(score>=34) return InpRiskT3;
   if(score>=17) return InpRiskT2;
   return InpRiskT1;
}

void AddTierCount(int score)
{
   if(score>=85) g_tier5++; else if(score>=68) g_tier4++; else if(score>=51) g_tier3++;
   else if(score>=34) g_tier2++; else if(score>=17) g_tier1++; else g_tier0++;
}

double GetRealizedVol()
{
   int need = InpRVolPeriod+1;
   double cl[]; ArraySetAsSeries(cl, true);
   int copied = CopyClose(_Symbol, PERIOD_D1, 1, need, cl);
   if(copied < need) return 18.0;
   double sum=0, sum2=0;
   for(int i=0; i<InpRVolPeriod; i++)
   {
      if(cl[i+1] <= 0) return 18.0;
      double r = MathLog(cl[i]/cl[i+1]);
      sum += r; sum2 += r*r;
   }
   double mean = sum/InpRVolPeriod;
   double var = sum2/InpRVolPeriod - mean*mean;
   if(var<=0) return 0;
   return MathSqrt(var) * MathSqrt(252.0) * 100.0;
}

//+------------------------------------------------------------------+
//| [MS-1] Percentila RVol de azi vs. seria rolling cauzala a        |
//| simbolului (fereastra InpRVolPercDays). Ruleaza o data pe zi la  |
//| evaluarea OR, cost neglijabil. Istoric insuficient -> 50 (neutru,|
//| consistent cu fallback-ul 18.0 din GetRealizedVol pe legacy).    |
//+------------------------------------------------------------------+
double GetRealizedVolPercentile(double rvToday)
{
   int win  = InpRVolPeriod;
   int hist = InpRVolPercDays;
   int need = win + hist + 1;
   double cl[]; ArraySetAsSeries(cl, true);
   if(CopyClose(_Symbol, PERIOD_D1, 1, need, cl) < need) return 50.0;
   int below = 0, total = 0;
   for(int k=1; k<=hist; k++)
   {
      double sum=0, sum2=0;
      bool bad=false;
      for(int i=0; i<win; i++)
      {
         double a = cl[k+i], b = cl[k+i+1];
         if(a<=0 || b<=0) { bad=true; break; }
         double r = MathLog(a/b);
         sum += r; sum2 += r*r;
      }
      if(bad) continue;
      double mean = sum/win;
      double var  = sum2/win - mean*mean;
      double v = (var>0) ? MathSqrt(var)*MathSqrt(252.0)*100.0 : 0.0;
      if(v < rvToday) below++;
      total++;
   }
   if(total < 20) return 50.0;
   return 100.0 * below / total;
}

double GetATR()
{
   int calc = BarsCalculated(g_atrHandle);
   if(calc<=0) return 0;
   double buf[]; ArraySetAsSeries(buf, true);
   if(CopyBuffer(g_atrHandle, 0, 1, 1, buf) < 1) return 0;
   return buf[0];
}

double GetSpreadBuffer()
{
   if(InpSpreadMult<=0) return 0;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double live = ask-bid;
   long sp = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double bs = sp*_Point;
   double base = MathMax(live, bs);
   if(base < _Point) base = _Point;
   return base * InpSpreadMult;
}

//+------------------------------------------------------------------+
//| GUARDS                                                             |
//+------------------------------------------------------------------+
bool SpreadOK()
{
   if(InpMaxSpreadPts <= 0) return true;
   long sp = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(sp > InpMaxSpreadPts)
   { PrintFormat("[GUARD] Spread %d > max %d - skip", (int)sp, InpMaxSpreadPts); return false; }
   return true;
}

bool TradingEnvOK()
{
   if(!InpRequireTradingEnv) return true;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) { Print("[GUARD] AutoTrading DISABLED"); return false; }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))           { Print("[GUARD] Expert trade disabled"); return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))   { Print("[GUARD] Account TRADE not allowed"); return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))    { Print("[GUARD] Account EA trading not allowed"); return false; }
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid <= 0 || ask <= 0) { PrintFormat("[GUARD] Bid/Ask invalid"); return false; }
   return true;
}

bool FreeMarginOK(bool buyDir, double lots)
{
   if(!InpRequireFreeMargin) return true;
   double price = buyDir ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   ENUM_ORDER_TYPE ot = buyDir ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double marginReq = 0;
   if(!OrderCalcMargin(ot, _Symbol, lots, price, marginReq))
   { PrintFormat("[GUARD] OrderCalcMargin FAIL code=%d", GetLastError()); return true; }
   if(marginReq >= freeMargin * 0.9)
   { PrintFormat("[GUARD] Free margin insuficient req=%.2f free=%.2f", marginReq, freeMargin); return false; }
   return true;
}

// [FIX-1] STOPS_LEVEL check — broker minimum distance entry-SL
bool StopsLevelOK(double entryPrice, double sl)
{
   if(!InpCheckStopsLevel) return true;
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel <= 0) return true;
   double minDist = stopsLevel * _Point;
   double dist = MathAbs(entryPrice - sl);
   if(dist < minDist)
   {
      PrintFormat("[FIX-1] StopsLevel SKIP: dist=%.4f < min=%.4f (level=%d pts)",
                  dist, minDist, (int)stopsLevel);
      return false;
   }
   return true;
}

// [FIX-4] FREEZE_LEVEL check — broker reject PositionModify in zona freeze
bool FreezeLevelOK(double newSL)
{
   if(!InpCheckFreezeLevel) return true;
   long freezeLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   if(freezeLevel <= 0) return true;
   double freeze = freezeLevel * _Point;
   double curPrice = g_isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double distToSL = MathAbs(curPrice - newSL);
   if(distToSL < freeze)
   {
      PrintFormat("[FIX-4] FreezeLevel SKIP TRAIL: dist=%.4f < freeze=%.4f (level=%d pts)",
                  distToSL, freeze, (int)freezeLevel);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| BUILD OR                                                           |
//+------------------------------------------------------------------+
bool BuildOR(datetime serverTime)
{
   g_orHigh = 0; g_orLow = DBL_MAX;
   int orStart = InpORStartH*60 + InpORStartM;
   int orEnd   = InpOREndH*60   + InpOREndM;
   string today = NYDateStr(serverTime);
   int found = 0;
   int totalBars = Bars(_Symbol, PERIOD_M5);
   int maxScan = (totalBars-1 < 40) ? totalBars-1 : 40;
   if(maxScan < 1) return false;

   for(int sh=1; sh<=maxScan; sh++)
   {
      datetime bt = iTime(_Symbol, PERIOD_M5, sh);
      if(bt==0) continue;
      if(NYDateStr(bt) != today) break;
      int bMin = NYMinOfDay(bt);
      if(bMin >= orStart && bMin < orEnd)
      {
         double h = iHigh(_Symbol, PERIOD_M5, sh);
         double l = iLow(_Symbol, PERIOD_M5, sh);
         if(h > g_orHigh) g_orHigh = h;
         if(l < g_orLow)  g_orLow  = l;
         found++;
      }
   }
   if(found < 2 || g_orHigh<=0 || g_orLow>=DBL_MAX)
   { PrintFormat("BuildOR FAIL: %d bare", found); return false; }
   return true;
}

void ComputeSMCFeatures(datetime serverTime)
{
   g_fvg_bull = false; g_fvg_bear = false;
   g_choch_bull = false; g_choch_bear = false;
   int orStart = InpORStartH*60 + InpORStartM;
   int orEnd   = InpOREndH*60   + InpOREndM;
   string today = NYDateStr(serverTime);

   double h_arr[]; ArrayResize(h_arr, 0);
   double l_arr[]; ArrayResize(l_arr, 0);
   int totalBars = Bars(_Symbol, PERIOD_M5);
   int maxScan = (totalBars-1<40)?totalBars-1:40;

   for(int sh=maxScan; sh>=1; sh--)
   {
      datetime bt = iTime(_Symbol, PERIOD_M5, sh);
      if(bt==0) continue;
      if(NYDateStr(bt) != today) continue;
      int bMin = NYMinOfDay(bt);
      if(bMin >= orStart && bMin < orEnd)
      {
         int n = ArraySize(h_arr);
         ArrayResize(h_arr, n+1); h_arr[n] = iHigh(_Symbol, PERIOD_M5, sh);
         ArrayResize(l_arr, n+1); l_arr[n] = iLow(_Symbol,  PERIOD_M5, sh);
      }
   }
   int N = ArraySize(h_arr);
   if(N < 3) return;

   if(l_arr[N-1] > h_arr[0]) g_fvg_bull = true;
   if(h_arr[N-1] < l_arr[0]) g_fvg_bear = true;
   int mid = N/2;
   if(l_arr[mid] < l_arr[0] && h_arr[N-1] > h_arr[0]) g_choch_bull = true;
   if(h_arr[mid] > h_arr[0] && l_arr[N-1] < l_arr[0]) g_choch_bear = true;
}

void ComputeLondonRange(datetime serverTime)
{
   g_lon_high = 0; g_lon_low = DBL_MAX;
   string today = NYDateStr(serverTime);
   int totalBars = Bars(_Symbol, PERIOD_M5);
   int maxScan = (totalBars-1<300)?totalBars-1:300;
   for(int sh=1; sh<=maxScan; sh++)
   {
      datetime bt = iTime(_Symbol, PERIOD_M5, sh);
      if(bt==0) continue;
      if(NYDateStr(bt) != today) break;
      MqlDateTime btd; TimeToStruct(bt, btd);
      int bMinBroker = btd.hour*60 + btd.min;
      if(bMinBroker >= 9*60 && bMinBroker < 16*60)
      {
         double h = iHigh(_Symbol, PERIOD_M5, sh);
         double l = iLow(_Symbol,  PERIOD_M5, sh);
         if(h > g_lon_high) g_lon_high = h;
         if(l < g_lon_low ) g_lon_low  = l;
      }
   }
   if(g_lon_low >= DBL_MAX) g_lon_low = 0;
}

void ComputeTrendFeatures()
{
   g_ema_align_bull = false; g_ema_align_bear = false;
   if(g_h_ema50 == INVALID_HANDLE || g_h_ema200 == INVALID_HANDLE) return;
   double e50[], e200[], cl[];
   ArraySetAsSeries(e50, true); ArraySetAsSeries(e200, true); ArraySetAsSeries(cl, true);
   if(CopyBuffer(g_h_ema50,  0, 1, 1, e50)  < 1) return;
   if(CopyBuffer(g_h_ema200, 0, 1, 1, e200) < 1) return;
   if(CopyClose(_Symbol, PERIOD_D1, 1, 1, cl) < 1) return;
   if(cl[0] > e50[0] && e50[0] > e200[0]) g_ema_align_bull = true;
   if(cl[0] < e50[0] && e50[0] < e200[0]) g_ema_align_bear = true;
}

void ComputeRSI()
{
   g_rsi_orb = 50.0;
   if(g_h_rsi == INVALID_HANDLE) return;
   double buf[]; ArraySetAsSeries(buf, true);
   if(CopyBuffer(g_h_rsi, 0, 1, 1, buf) >= 1) g_rsi_orb = buf[0];
}

int EvaluateOR(datetime serverTime)
{
   int orStart = InpORStartH*60+InpORStartM;
   int orEnd   = InpOREndH*60+InpOREndM;
   string today = NYDateStr(serverTime);
   int firstSh=-1, lastSh=-1;
   int totalBars = Bars(_Symbol, PERIOD_M5);
   int maxScan = (totalBars-1<40)?totalBars-1:40;
   for(int sh=1; sh<=maxScan; sh++)
   {
      datetime bt = iTime(_Symbol, PERIOD_M5, sh);
      if(bt==0) continue;
      if(NYDateStr(bt) != today) break;
      int bMin = NYMinOfDay(bt);
      if(bMin>=orStart && bMin<orEnd)
      {
         if(sh>firstSh) firstSh=sh;
         if(lastSh<0 || sh<lastSh) lastSh=sh;
      }
   }
   if(firstSh<0 || lastSh<0) return 50;

   double orOpen  = iOpen(_Symbol, PERIOD_M5, firstSh);
   double orClose = iClose(_Symbol, PERIOD_M5, lastSh);
   double orRng = g_orHigh - g_orLow;
   if(orRng<=0) return 50;
   double bodyPct = MathAbs(orClose-orOpen)/orRng*100;
   double uwPct   = (g_orHigh - MathMax(orOpen, orClose))/orRng*100;
   double cPos    = (orClose - g_orLow)/orRng*100;
   double rVol    = GetRealizedVol();

   // [MS-1] scor legacy (praguri absolute) sau normalizat (multi-symbol)
   int score;
   if(InpScoreMode == SCORE_ATR_NORM)
   {
      double atrD = GetATR();
      double orATRratio = (atrD > 0) ? orRng/atrD : 0.0;
      double rvPct = GetRealizedVolPercentile(rVol);
      score = ComputeScoreNorm(bodyPct, uwPct, orATRratio, rvPct, cPos);
      PrintFormat("OR SCORE=%d [ATR_NORM] | Body=%.0f%% UW=%.0f%% Range=%.1f (%.3fxATR) RVol=%.1f (p%.0f) CPos=%.0f%%",
                  score, bodyPct, uwPct, orRng, orATRratio, rVol, rvPct, cPos);
   }
   else
   {
      score = ComputeScore(bodyPct, uwPct, orRng, rVol, cPos);
      PrintFormat("OR SCORE=%d | Body=%.0f%% UW=%.0f%% Range=%.1f RVol=%.1f CPos=%.0f%%",
                  score, bodyPct, uwPct, orRng, rVol, cPos);
   }

   g_close_loc = cPos / 100.0;
   g_close_loc_ext_long  = (g_close_loc >= 0.80);
   g_close_loc_ext_short = (g_close_loc <= 0.20);
   ComputeSMCFeatures(serverTime);
   ComputeLondonRange(serverTime);
   ComputeTrendFeatures();
   ComputeRSI();

   return score;
}

int ConfluencesAligned(bool isBuy)
{
   int conf = 0;
   if(isBuy)
   {
      if(g_fvg_bull) conf++;
      if(g_choch_bull) conf++;
      if(g_lon_high>0 && g_orHigh > g_lon_high) conf++;
      if(g_ema_align_bull) conf++;
      if(g_rsi_orb >= 65) conf++;
   }
   else
   {
      if(g_fvg_bear) conf++;
      if(g_choch_bear) conf++;
      if(g_lon_low>0 && g_orLow < g_lon_low) conf++;
      if(g_ema_align_bear) conf++;
      if(g_rsi_orb <= 35) conf++;
   }
   return conf;
}

double ComputeRiskMultiplier(bool isBuy, int score, datetime serverTime)
{
   if(!InpEnableRiskMod) return 1.0;
   double m = 1.0;
   int conf = ConfluencesAligned(isBuy);
   bool extreme_aligned = (isBuy && g_close_loc_ext_long) || (!isBuy && g_close_loc_ext_short);
   bool sweep_aligned = (isBuy && g_lon_high>0 && g_orHigh>g_lon_high)
                      || (!isBuy && g_lon_low>0 && g_orLow<g_lon_low);
   bool fvg_aligned = (isBuy && g_fvg_bull) || (!isBuy && g_fvg_bear);
   bool trend_align_dir = (isBuy && g_ema_align_bull) || (!isBuy && g_ema_align_bear);
   bool trend_conflict  = (isBuy && g_ema_align_bear) || (!isBuy && g_ema_align_bull);
   int dow = NYDayOfWeek(serverTime);
   bool isMonday = (dow == 1);
   bool mid_no_momentum = (g_rsi_orb > 40 && g_rsi_orb < 60 && g_close_loc > 0.4 && g_close_loc < 0.6);

   if(InpModScheme == MOD_AGGRESSIVE || InpModScheme == MOD_SOFT)
   {
      bool isSoft = (InpModScheme == MOD_SOFT);
      if(conf >= 3) m *= isSoft ? (1.0+(InpMultConfHigh-1.0)*0.7) : InpMultConfHigh;
      if(extreme_aligned && score >= 51) m *= isSoft ? (1.0+(InpMultExtremeStrong-1.0)*0.7) : InpMultExtremeStrong;
      if(fvg_aligned && trend_align_dir) m *= isSoft ? (1.0+(InpMultFVGTrend-1.0)*0.7) : InpMultFVGTrend;
      if(sweep_aligned) m *= isSoft ? (1.0+(InpMultSweepLondon-1.0)*0.7) : InpMultSweepLondon;
      if(score < 17) m *= isSoft ? (1.0-(1.0-InpMultScoreLow)*0.7) : InpMultScoreLow;
      if(isMonday)   m *= isSoft ? (1.0-(1.0-InpMultMonday)*0.7) : InpMultMonday;
      if(mid_no_momentum) m *= isSoft ? (1.0-(1.0-InpMultMidNoMomentum)*0.7) : InpMultMidNoMomentum;
      if(trend_conflict)  m *= isSoft ? (1.0-(1.0-InpMultTrendConflict)*0.7) : InpMultTrendConflict;
   }
   else if(InpModScheme == MOD_CONSERVATIVE)
   {
      if(score < 17) m *= InpMultScoreLow * 0.85;
      if(isMonday)   m *= InpMultMonday * 0.90;
      if(mid_no_momentum) m *= InpMultMidNoMomentum * 0.90;
      if(trend_conflict)  m *= InpMultTrendConflict * 0.95;
   }
   else if(InpModScheme == MOD_AMPLIFIER)
   {
      if(conf >= 3) m *= InpMultConfHigh * 1.05;
      if(extreme_aligned && score >= 51) m *= InpMultExtremeStrong * 1.05;
      if(fvg_aligned) m *= InpMultFVGTrend;
      if(sweep_aligned) m *= InpMultSweepLondon;
   }

   if(m < InpMultMin) m = InpMultMin;
   if(m > InpMultMax) m = InpMultMax;
   return m;
}

//+------------------------------------------------------------------+
//| [INST] Multiplicatori institutionali — NU taie trade-uri,        |
//| moduleaza riscul. Apelat din OpenTrade cand bara de semnal e     |
//| inca shift 1 (paritate cu research-ul v6next walk-forward).      |
//+------------------------------------------------------------------+
double ComputeInstitutionalMult(bool isBuy)
{
   double m = 1.0;
   g_instChase = false;
   g_instRSIBoost = false;

   // [INST-1] ANTI-CHASE: range-ul barei de semnal (shift 1) vs media
   // True Range a barelor precedente (shift 2..1+period, medie simpla)
   if(InpChaseEnable && InpChaseATRMax > 0 && InpChaseRiskMult > 0)
   {
      double h1 = iHigh(_Symbol, PERIOD_M5, 1);
      double l1 = iLow(_Symbol, PERIOD_M5, 1);
      double rngSig = h1 - l1;
      double sum = 0; int cnt = 0;
      for(int sh = 2; sh <= 1 + InpChaseATRPeriod; sh++)
      {
         double h  = iHigh(_Symbol, PERIOD_M5, sh);
         double l  = iLow(_Symbol, PERIOD_M5, sh);
         double pc = iClose(_Symbol, PERIOD_M5, sh + 1);
         if(h <= 0 || l <= 0 || pc <= 0) break;
         sum += MathMax(h - l, MathMax(MathAbs(h - pc), MathAbs(l - pc)));
         cnt++;
      }
      if(cnt >= 5 && rngSig > 0)
      {
         double atr = sum / cnt;
         if(atr > 0 && rngSig / atr > InpChaseATRMax)
         {
            m *= InpChaseRiskMult;
            g_instChase = true;
            PrintFormat("[INST-1 ANTI-CHASE] rng_semnal=%.1f ATR%d=%.1f ratio=%.2f > %.2f -> risc x%.2f",
                        rngSig, InpChaseATRPeriod, atr, rngSig/atr, InpChaseATRMax, InpChaseRiskMult);
         }
      }
   }

   // [INST-2] RSI TIER BOOST: g_rsi_orb = RSI14(M5) la ultima bara OR
   // (calculat deja in EvaluateOR), aliniat cu directia, fara luni
   if(InpRSIBoostEnable && InpRSIBoostMult > 0)
   {
      bool mondayNY = (NYDayOfWeek(TimeCurrent()) == 1);
      bool aligned = (isBuy && g_rsi_orb >= InpRSIBoostHi) ||
                     (!isBuy && g_rsi_orb <= InpRSIBoostLo);
      if(aligned)
      {
         if(InpRSIBoostSkipMon && mondayNY)
            PrintFormat("[INST-2 RSI BOOST] RSI_OR=%.1f aliniat, dar e LUNI -> fara boost", g_rsi_orb);
         else
         {
            m *= InpRSIBoostMult;
            g_instRSIBoost = true;
            PrintFormat("[INST-2 RSI BOOST] RSI_OR=%.1f %s -> risc x%.2f",
                        g_rsi_orb, isBuy ? ">=Hi" : "<=Lo", InpRSIBoostMult);
         }
      }
   }
   return m;
}

//+------------------------------------------------------------------+
//| POSITION HELPERS — folosesc g_actualMagic                         |
//+------------------------------------------------------------------+
bool HasMyPosition()
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      if(PositionGetTicket(i)==0) continue;
      if(PositionGetInteger(POSITION_MAGIC)==(long)g_actualMagic && PositionGetString(POSITION_SYMBOL)==_Symbol) return true;
   }
   return false;
}

ulong GetMyTicket()
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t==0) continue;
      if(PositionGetInteger(POSITION_MAGIC)==(long)g_actualMagic && PositionGetString(POSITION_SYMBOL)==_Symbol) return t;
   }
   return 0;
}

double CalcLot(double riskPts)
{
   if(riskPts<=0) return 0;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double tickVal = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize= SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lotMin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double lotMax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(tickVal<=0||tickSize<=0||lotStep<=0) { Print("[GUARD] Spec invalid"); return lotMin; }
   double riskPerLot = (riskPts/tickSize)*tickVal;
   if(riskPerLot<=0) return lotMin;
   double riskMoney = equity*g_riskPct/100.0;
   double lots = riskMoney/riskPerLot;
   lots = MathFloor(lots/lotStep)*lotStep;
   lots = MathMax(lots, lotMin);
   lots = MathMin(lots, lotMax);
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| [MANUAL RESET] close manual = client/mobil/web (nu SL/TP/EXPERT)  |
//+------------------------------------------------------------------+
bool IsManualDealReason(long r)
{
   return (r==DEAL_REASON_CLIENT || r==DEAL_REASON_MOBILE || r==DEAL_REASON_WEB);
}

//+------------------------------------------------------------------+
//| [TIER PREVIEW] SL (puncte) estimat pentru calculul lotului        |
//+------------------------------------------------------------------+
double EstimateTierSLpts()
{
   if(InpTierLotSLpts > 0)              return InpTierLotSLpts;        // 1) override fix
   if(g_riskPoints > 0 && HasMyPosition()) return g_riskPoints;       // 2) trade activ -> SL real
   if(g_orRange > 0)                    return g_orRange;             // 3) OR format azi -> proxy SL
   double atr = GetATR();
   if(atr > 0)                          return atr * InpTierLotSLfromATR; // 4) fallback ATR D1
   double px = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(px > 0)                           return px * 0.005;            // [MS-2] fallback generic ~0.5% din pret
   return 150.0;                                                       // 5) ultima resorta
}

//+------------------------------------------------------------------+
//| [TIER PREVIEW] Lot pentru un risk% dat si SL dat (ca CalcLot)     |
//+------------------------------------------------------------------+
double CalcLotForRiskPct(double riskPts, double riskPct)
{
   if(riskPts<=0 || riskPct<=0) return 0;
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double tickVal = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize= SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lotMin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double lotMax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(tickVal<=0||tickSize<=0||lotStep<=0) return 0;
   double riskPerLot = (riskPts/tickSize)*tickVal;
   if(riskPerLot<=0) return 0;
   double lots = (equity*riskPct/100.0)/riskPerLot;
   lots = MathFloor(lots/lotStep)*lotStep;
   lots = MathMax(lots, lotMin);
   lots = MathMin(lots, lotMax);
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| [TIER PREVIEW] Text panou: lotul pe fiecare tier (AZI/A DOUA ZI)  |
//| - daca NU s-a tradat azi  -> intrarea de AZI                      |
//| - daca trade-ul de azi e gata/in curs -> intrarea de A DOUA ZI    |
//+------------------------------------------------------------------+
string BuildTierLotText()
{
   if(!InpShowTierLots) return "";

   double slPts = EstimateTierSLpts();

   bool nextDayMode = g_traded;        // azi terminat (trade facut sau skip) -> arata maine
   bool inTrade     = HasMyPosition();

   // Martingala pentru intrarea afisata:
   //  AZI (pre-trade): g_lastWasLoss = ce se aplica azi
   //  A DOUA ZI (dupa close): g_lastWasLoss reflecta deja rezultatul de azi
   //                          (close manual => OFF prin InpResetMartOnManual)
   //  inca in trade: rezultatul nu e cunoscut -> mart PENDING
   bool martKnown = !(nextDayMode && inTrade);
   bool martOn    = (InpUseMart && g_lastWasLoss);

   double tiers[6];
   tiers[0]=InpRiskT1; tiers[1]=InpRiskT2; tiers[2]=InpRiskT3;
   tiers[3]=InpRiskT4; tiers[4]=InpRiskT5; tiers[5]=InpRiskT6;

   string head = nextDayMode
      ? StringFormat("--- LOT / TIER (A DOUA ZI) | SL~%.0fp ---", slPts)
      : StringFormat("--- LOT / TIER (AZI) | SL~%.0fp ---", slPts);

   string martLine;
   if(!martKnown)
      martLine = "Mart: PENDING (depinde de rezultatul trade-ului curent)";
   else
      martLine = StringFormat("Mart: %s%s | Cap %.2f%%",
                   martOn ? StringFormat("ON x%.3f", InpMartMult) : "OFF",
                   (g_lastCloseManual && !martOn) ? " (manual reset)" : "",
                   InpRiskCap);
   if(InpPortfolioScale != 1.0)
      martLine += StringFormat(" | Scale x%.2f", InpPortfolioScale);

   string lotsLine = "";
   for(int i=0;i<6;i++)
   {
      double rp = tiers[i];
      if(martKnown && martOn) rp = MathMin(rp*InpMartMult, InpRiskCap);
      rp *= InpPortfolioScale;   // [MS-3] preview consistent cu sizing-ul real
      double lot = CalcLotForRiskPct(slPts, rp);
      lotsLine += StringFormat("T%d %.2f%%=%.2f", i+1, rp, lot);
      lotsLine += (i==2) ? "\n" : (i==5 ? "" : "  ");
   }

   string foot = StringFormat("(*) lot la mult=1.0; la semnal x[%.2f..%.2f], Luni x%.2f",
                              InpMultMin, InpMultMax, InpMultMonday);

   return StringFormat("%s\n%s\n%s\n%s", head, martLine, lotsLine, foot);
}

//+------------------------------------------------------------------+
//| OPEN TRADE — cu [FIX-1] StopsLevel                                |
//+------------------------------------------------------------------+
bool OpenTrade(bool buyDir, double sl)
{
   if(!TradingEnvOK()) return false;
   if(!SpreadOK()) return false;

   double spreadBuf = GetSpreadBuffer();
   if(buyDir) sl -= spreadBuf;
   else       sl += spreadBuf;
   sl = NormalizeDouble(sl, _Digits);

   double price = buyDir ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double rPts = buyDir ? (price-sl) : (sl-price);
   if(rPts<=0) { PrintFormat("SKIP: riskPts=%.2f<=0", rPts); return false; }

   // [FIX-1] StopsLevel check
   if(!StopsLevelOK(price, sl)) return false;

   double mult  = ComputeRiskMultiplier(buyDir, g_lastScore, TimeCurrent());
   double instM = ComputeInstitutionalMult(buyDir);   // [INST-1/2] anti-chase + RSI boost
   double riskPctOriginal = g_riskPct;
   g_riskPct = MathMin(g_riskPct * mult * instM, InpRiskCap);
   g_riskPct *= InpPortfolioScale;   // [MS-3] impartire risc intre instante multi-symbol
   g_lastMultiplier = mult * instM;

   double lots = CalcLot(rPts);
   if(lots<=0) { Print("SKIP: lots=0"); g_riskPct = riskPctOriginal; return false; }
   if(!FreeMarginOK(buyDir, lots)) { g_riskPct = riskPctOriginal; return false; }

   string comment = StringFormat("V1I s%d R%.4f m%.2f i%.2f", g_lastScore, g_riskPct, mult, instM);

   bool ok=false; uint rc=0;
   for(int a=1; a<=InpMaxRetry; a++)
   {
      price = buyDir ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(buyDir) ok = trade.Buy(lots, _Symbol, 0, sl, 0, comment);
      else       ok = trade.Sell(lots, _Symbol, 0, sl, 0, comment);
      rc = trade.ResultRetcode();
      if(ok && (rc==TRADE_RETCODE_DONE || rc==TRADE_RETCODE_PLACED)) break;
      PrintFormat("OPEN attempt %d/%d FAIL rc=%d %s", a, InpMaxRetry, rc, trade.ResultComment());
      if(a<InpMaxRetry) Sleep(InpRetryDelayMs);
   }
   if(!ok || (rc!=TRADE_RETCODE_DONE && rc!=TRADE_RETCODE_PLACED))
   { PrintFormat("OPEN FINAL FAIL rc=%d", rc); g_riskPct = riskPctOriginal; return false; }

   g_entryPrice = trade.ResultPrice();
   if(g_entryPrice<=0) g_entryPrice = price;
   g_stopLoss = sl;
   g_riskPoints = buyDir ? (g_entryPrice-sl) : (sl-g_entryPrice);
   if(g_riskPoints<=0) g_riskPoints = rPts;
   g_trailBest = g_entryPrice;
   g_trailActive = false;
   g_isBuy = buyDir;
   g_hadPosition = true;
   g_tp1Done = false; g_tp2Done = false;
   g_totalTrades++;
   AddTierCount(g_lastScore);

   DrawTradeLines();

   double daysSinceLastClose = 0;
   if(g_lastCloseTime > 0)
      daysSinceLastClose = (double)(TimeCurrent() - g_lastCloseTime) / 86400.0;

   PrintFormat("OPEN %s @%.2f SL=%.2f R=%.2f Lot=%.2f Score=%d Risk=%.4f%% mult=%.2f inst=%.2f%s%s",
               buyDir?"BUY":"SELL", g_entryPrice, sl, g_riskPoints, lots, g_lastScore, g_riskPct, mult, instM,
               g_instChase?" [CHASE]":"", g_instRSIBoost?" [RSI+]":"");
   PrintFormat("[MART STATE] %s | BaseRisk=%.4f%% -> Final=%.4f%% | LastClose: %s (%.1f zile)",
               (InpUseMart && g_lastWasLoss) ? StringFormat("ON x%.3f", InpMartMult) : "OFF (base, mart reset)",
               g_baseRiskPct, g_riskPct,
               g_lastCloseTime > 0 ? TimeToString(g_lastCloseTime, TIME_DATE|TIME_MINUTES) : "N/A",
               daysSinceLastClose);
   return true;
}

bool PartialClose(double closePct, string reason)
{
   ulong t = GetMyTicket(); if(t==0) return false;
   if(!PositionSelectByTicket(t)) return false;
   double curVol = PositionGetDouble(POSITION_VOLUME);
   double lotStep= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double lotMin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double cv = curVol*closePct/100.0;
   cv = MathFloor(cv/lotStep)*lotStep;
   if(cv<lotMin)
   { PrintFormat("PARTIAL SKIP [%s] -> close FULL", reason); return CloseMyPosition(reason+"_FULL"); }
   double rest = curVol - cv;
   if(rest>0 && rest<lotMin) cv = curVol;
   if(!trade.PositionClosePartial(t, cv, (ulong)InpSlippage))
   { PrintFormat("PARTIAL ERR [%s] rc=%d", reason, trade.ResultRetcode()); return false; }
   PrintFormat("PARTIAL CLOSE [%s]: %.2f din %.2f (%.0f%%)", reason, cv, curVol, closePct);
   return true;
}

//+------------------------------------------------------------------+
//| CLOSE                                                              |
//+------------------------------------------------------------------+
bool CloseMyPosition(string reason)
{
   ulong t = GetMyTicket(); if(t==0) return false;
   if(!PositionSelectByTicket(t)) return false;
   double op = PositionGetDouble(POSITION_PRICE_OPEN);
   bool isBuyP = (PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY);
   if(!trade.PositionClose(t, (ulong)InpSlippage))
   { PrintFormat("CLOSE ERR [%s] rc=%d", reason, trade.ResultRetcode()); return false; }

   double pnl = 0; bool found = false;
   if(HistorySelect(TimeCurrent()-60, TimeCurrent()))
   {
      for(int i=HistoryDealsTotal()-1; i>=0; i--)
      {
         ulong dt = HistoryDealGetTicket(i);
         if(dt==0) continue;
         if(HistoryDealGetInteger(dt, DEAL_MAGIC)!=(long)g_actualMagic) continue;
         if(HistoryDealGetString(dt, DEAL_SYMBOL)!=_Symbol) continue;
         if(HistoryDealGetInteger(dt, DEAL_ENTRY)!=DEAL_ENTRY_OUT) continue;
         pnl = HistoryDealGetDouble(dt, DEAL_PROFIT)+HistoryDealGetDouble(dt, DEAL_SWAP)+HistoryDealGetDouble(dt, DEAL_COMMISSION);
         found=true; break;
      }
   }
   if(!found)
   {
      double cp = isBuyP?SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      pnl = isBuyP?(cp-op):(op-cp);
   }

   bool prevMart = g_lastWasLoss;
   if(reason == "NEW_DAY" && !InpMartOnNewDayClose)
   {
      g_lastWasLoss = false;
      PrintFormat("[NEW_DAY] close pnl=%.2f | Mart fortat OFF (accident)", pnl);
   }
   else g_lastWasLoss = (pnl < 0);

   g_hadPosition = false;
   if(pnl >= 0) g_wins++; else g_losses++;
   g_lastCloseTime = TimeCurrent();

   DeleteTradeLines();

   if(pnl >= 0)
      PrintFormat("[MART RESET] WIN pnl=%.2f | g_lastWasLoss: %s -> OFF", pnl, prevMart?"ON":"OFF");
   else if(reason != "NEW_DAY" || InpMartOnNewDayClose)
      PrintFormat("[MART ARM] LOSS pnl=%.2f | next trade x%.3f", pnl, InpMartMult);

   PrintFormat("CLOSE [%s] PnL=%.2f Score=%d Mult=%.2f Mart=%s",
               reason, pnl, g_lastScore, g_lastMultiplier, g_lastWasLoss?"NEXT ON":"OFF");
   return true;
}

//+------------------------------------------------------------------+
//| TRAIL — cu [FIX-4] FreezeLevel + update live line                 |
//+------------------------------------------------------------------+
void TrailSL(double newSL)
{
   ulong t = GetMyTicket(); if(t==0) return;
   if(!PositionSelectByTicket(t)) return;
   newSL = NormalizeDouble(newSL, _Digits);
   if(g_isBuy && newSL<g_entryPrice-_Point) newSL=g_entryPrice-_Point;
   if(!g_isBuy && newSL>g_entryPrice+_Point) newSL=g_entryPrice+_Point;
   double curSL = PositionGetDouble(POSITION_SL);
   double curTP = PositionGetDouble(POSITION_TP);
   if(MathAbs(newSL-curSL) < _Point*2.0) return;
   if(g_isBuy && newSL<=curSL) return;
   if(!g_isBuy && newSL>=curSL) return;

   // [FIX-4] FreezeLevel check
   if(!FreezeLevelOK(newSL)) return;

   if(!trade.PositionModify(t, newSL, curTP))
      PrintFormat("TRAIL ERR rc=%d %s", trade.ResultRetcode(), trade.ResultComment());
   else
   {
      PrintFormat("TRAIL SL: %.2f -> %.2f", curSL, newSL);
      g_stopLoss = newSL;
      UpdateTrailLiveLine();  // actualizeaza linia vizuala
   }
}

void MoveSLtoBreakeven()
{
   double be = g_isBuy ? g_entryPrice+_Point : g_entryPrice-_Point;
   if(g_isBuy && be<=g_stopLoss) return;
   if(!g_isBuy && be>=g_stopLoss) return;
   ulong t = GetMyTicket(); if(t==0) return;
   if(!PositionSelectByTicket(t)) return;
   be = NormalizeDouble(be, _Digits);

   // [FIX-4] FreezeLevel check si pe BE move
   if(!FreezeLevelOK(be)) { Print("[FIX-4] BE move blocked by freeze - retry urmator"); return; }

   double curTP = PositionGetDouble(POSITION_TP);
   if(!trade.PositionModify(t, be, curTP))
      PrintFormat("BE ERR rc=%d", trade.ResultRetcode());
   else { PrintFormat("SL -> BE @%.2f", be); g_stopLoss = be; }
}

//+------------------------------------------------------------------+
//| EXTERNAL CLOSE                                                     |
//+------------------------------------------------------------------+
void CheckExternalClose()
{
   if(!g_hadPosition) return;
   if(HasMyPosition()) return;
   g_hadPosition = false;
   DeleteTradeLines();
   if(!HistorySelect(TimeCurrent()-86400, TimeCurrent())) return;
   for(int i=HistoryDealsTotal()-1; i>=0; i--)
   {
      ulong dt = HistoryDealGetTicket(i);
      if(dt==0) continue;
      if(HistoryDealGetInteger(dt, DEAL_MAGIC)!=(long)g_actualMagic) continue;
      if(HistoryDealGetString(dt, DEAL_SYMBOL)!=_Symbol) continue;
      if(HistoryDealGetInteger(dt, DEAL_ENTRY)!=DEAL_ENTRY_OUT) continue;
      double pnl = HistoryDealGetDouble(dt, DEAL_PROFIT)+HistoryDealGetDouble(dt, DEAL_SWAP)+HistoryDealGetDouble(dt, DEAL_COMMISSION);
      long  reason = HistoryDealGetInteger(dt, DEAL_REASON);
      bool  manual = IsManualDealReason(reason);
      bool prevMart = g_lastWasLoss;
      g_lastCloseManual = manual;
      if(manual && InpResetMartOnManual)
      {
         g_lastWasLoss = false;   // [MANUAL RESET] inchidere manuala -> NU armam mart pt ziua urmatoare
         PrintFormat("[MANUAL CLOSE] reason=%d pnl=%.2f -> Mart OFF (reset pt ziua urmatoare)", (int)reason, pnl);
      }
      else
         g_lastWasLoss = (pnl<0);
      if(pnl>=0) g_wins++; else g_losses++;
      g_lastCloseTime = (datetime)HistoryDealGetInteger(dt, DEAL_TIME);
      if(manual && InpResetMartOnManual) { /* deja logat mai sus */ }
      else if(pnl >= 0) PrintFormat("[EXT MART RESET] WIN pnl=%.2f | prev=%s -> OFF", pnl, prevMart?"ON":"OFF");
      else PrintFormat("[EXT MART ARM] LOSS pnl=%.2f -> next ON", pnl);
      PrintFormat("EXT CLOSE: PnL=%.2f reason=%d Mart=%s", pnl, (int)reason, g_lastWasLoss?"NEXT ON":"OFF");
      return;
   }
}

//+------------------------------------------------------------------+
//| RECOVERY — [FIX-7] g_baseRiskPct + [LIVE-5] lookback              |
//+------------------------------------------------------------------+
void RecoverAll()
{
   ulong t = GetMyTicket();
   if(t>0 && PositionSelectByTicket(t))
   {
      datetime posTime = (datetime)PositionGetInteger(POSITION_TIME);
      string posDay = NYDateStr(posTime);
      string nowDay = NYDateStr(TimeCurrent());
      if(posDay != nowDay)
      {
         PrintFormat("RECOVER: STALE %s -> close", posDay);
         g_hadPosition = true;
         CloseMyPosition("STALE_OVERNIGHT");
         g_traded = true;
         return;
      }
      g_entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      g_stopLoss = PositionGetDouble(POSITION_SL);
      g_isBuy = (PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY);
      g_traded = true; g_orDone = true; g_hadPosition = true;
      g_riskPoints = g_isBuy?(g_entryPrice-g_stopLoss):(g_stopLoss-g_entryPrice);
      if(g_riskPoints<=0) g_riskPoints=1;
      if(g_orRange<=0)
      {
         if(BuildOR(TimeCurrent())) g_orRange = g_orHigh-g_orLow;
         else g_orRange = g_riskPoints*0.6;
      }
      double cur = g_isBuy?SymbolInfoDouble(_Symbol,SYMBOL_BID):SymbolInfoDouble(_Symbol,SYMBOL_ASK);
      g_trailBest = g_isBuy?MathMax(cur,g_entryPrice):MathMin(cur,g_entryPrice);
      double curPnl = g_isBuy?(cur-g_entryPrice):(g_entryPrice-cur);
      g_trailActive = (g_orRange>0 && curPnl >= g_orRange*InpTrailAct);
      g_tp1Done = (g_isBuy && g_stopLoss>=g_entryPrice) || (!g_isBuy && g_stopLoss<=g_entryPrice);
      g_tp2Done = false;

      // [FIX-7] Setam g_baseRiskPct la recovery
      g_lastScore = 50;
      g_baseRiskPct = ScoreToRisk(g_lastScore);

      DrawORLines();
      DrawTradeLines();
      if(g_trailActive) UpdateTrailLiveLine();

      PrintFormat("RECOVER: %s @%.2f | Trail=%s", g_isBuy?"BUY":"SELL", g_entryPrice, g_trailActive?"ACTIVE":"WAIT");
   }
   else
   {
      g_traded = TradedToday();
      g_hadPosition = false;
      RecoverMart();
   }
}

bool TradedToday()
{
   datetime ds = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   if(!HistorySelect(ds, TimeCurrent())) return false;
   for(int i=HistoryDealsTotal()-1; i>=0; i--)
   {
      ulong dt = HistoryDealGetTicket(i);
      if(dt==0) continue;
      if(HistoryDealGetInteger(dt,DEAL_MAGIC)==(long)g_actualMagic &&
         HistoryDealGetString(dt,DEAL_SYMBOL)==_Symbol &&
         HistoryDealGetInteger(dt,DEAL_ENTRY)==DEAL_ENTRY_IN) return true;
   }
   return false;
}

void RecoverMart()
{
   // [MART RESET ONE-SHOT] Daca user a setat InpForceResetMart=true,
   // ignora total history si porneste cu MART OFF pentru sesiunea curenta.
   // MART se va re-activa automat la primul loss nou intra-sesiune.
   if(InpForceResetMart)
   {
      g_lastWasLoss = false;
      g_lastCloseTime = 0;
      g_hb_lastReason = "MART_RESET_FORCED";
      PrintFormat("[MART RESET FORCED] InpForceResetMart=true -> Mart OFF la start. "
                  "Se re-activeaza pe orice loss nou. (seteaza FALSE inapoi pentru comportament normal)");
      return;
   }

   datetime cutoff = TimeCurrent() - (datetime)InpMartLookbackDays * 86400;
   if(!HistorySelect(cutoff, TimeCurrent())) return;
   for(int i=HistoryDealsTotal()-1; i>=0; i--)
   {
      ulong dt = HistoryDealGetTicket(i);
      if(dt==0) continue;
      if(HistoryDealGetInteger(dt,DEAL_MAGIC)!=(long)g_actualMagic) continue;
      if(HistoryDealGetString(dt,DEAL_SYMBOL)!=_Symbol) continue;
      if(HistoryDealGetInteger(dt,DEAL_ENTRY)!=DEAL_ENTRY_OUT) continue;
      double pnl = HistoryDealGetDouble(dt,DEAL_PROFIT)+HistoryDealGetDouble(dt,DEAL_SWAP)+HistoryDealGetDouble(dt,DEAL_COMMISSION);
      long  reason = HistoryDealGetInteger(dt, DEAL_REASON);
      bool  manual = IsManualDealReason(reason);
      g_lastCloseManual = manual;
      g_lastCloseTime = (datetime)HistoryDealGetInteger(dt, DEAL_TIME);
      if(manual && InpResetMartOnManual)
      {
         g_lastWasLoss = false;   // [MANUAL RESET] ultimul close a fost manual -> Mart OFF
         PrintFormat("RecoverMart: ultimul deal MANUAL (reason=%d) pnl=%.2f -> Mart OFF (reset)", (int)reason, pnl);
      }
      else
      {
         g_lastWasLoss = (pnl<0);
         PrintFormat("RecoverMart: ultimul deal in %d zile pnl=%.2f reason=%d Mart=%s",
                     InpMartLookbackDays, pnl, (int)reason, g_lastWasLoss?"ON":"OFF");
      }
      // [INST-4] WEEK RESET: loss ramas din saptamana NY precedenta nu armeaza mart
      if(InpMartWeekReset && g_lastWasLoss && g_lastCloseTime > 0 &&
         NYWeekIndex(g_lastCloseTime) < NYWeekIndex(TimeCurrent()))
      {
         g_lastWasLoss = false;
         Print("[INST-4 WEEK RESET] ultimul loss e in saptamana NY precedenta -> Mart OFF");
      }
      return;
   }
   g_lastWasLoss = false;
   PrintFormat("RecoverMart: niciun deal in %d zile -> Mart OFF", InpMartLookbackDays);
}

//+------------------------------------------------------------------+
//| HEARTBEAT — afiseaza panou status pe chart + log periodic         |
//| Garantie ca vezi V6 e activa chiar daca sesiunea OR e ratata     |
//+------------------------------------------------------------------+
void V1I_ShowHeartbeat(int nyMin, int orStart, int orEnd, int eodMin, bool hasPos, bool orValid, bool tradedToday)
{
   if(!InpShowHeartbeat) return;

   // Determine status + color
   string state;
   color  stateColor;
   if(hasPos)                        { state="IN_TRADE";          stateColor=InpHB_ColorInTrade;  }
   else if(tradedToday)              { state="DONE_FOR_DAY";      stateColor=InpHB_ColorWaiting; }
   else if(nyMin>=eodMin)            { state="EOD_CLOSED";        stateColor=InpHB_ColorWaiting; }
   else if(nyMin<orStart)            { state="PRE_SESSION";       stateColor=InpHB_ColorWaiting; }
   else if(nyMin<orEnd)              { state="IN_OR_FORMING";     stateColor=InpHB_ColorActive;  }
   else if(orValid && !tradedToday)  { state="OR_DONE_WAITING";   stateColor=InpHB_ColorActive;  }
   else                              { state="WAITING";           stateColor=InpHB_ColorWaiting; }
   g_hb_lastSessionStatus = state;

   datetime now = TimeCurrent();
   long uptimeSec = (long)(now - g_hb_initTime);
   long uptimeH = uptimeSec/3600;
   long uptimeM = (uptimeSec%3600)/60;

   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   double ddPct = (bal>0) ? (bal-eq)/bal*100.0 : 0.0;

   int nyHour = nyMin/60;
   int nyMn   = nyMin%60;
   int orShH  = orStart/60, orShM=orStart%60;
   int orEhH  = orEnd/60,   orEhM=orEnd%60;

   string text = StringFormat(
      "ORB V1 INSTITUTIONAL [%s] | Magic=%llu\n"
      "Status: %s\n"
      "NY Time: %02d:%02d | OR window: %02d:%02d-%02d:%02d | EOD: %02d:%02d\n"
      "Uptime: %ldh %02ldm | Symbol: %s\n"
      "Bal=$%.2f  Eq=$%.2f  DD=%.2f%%\n"
      "OR: H=%.2f L=%.2f Range=%.2f Valid=%s\n"
      "Tier risk=%.3f%% Mult=%.2f Mart=%s\n"
      "Stats: Trades=%d W=%d L=%d WR=%.1f%%\n"
      "Last reason: %s",
      InpScoreMode==SCORE_ATR_NORM?"ATR_NORM":"LEGACY",
      g_actualMagic,
      state,
      nyHour, nyMn, orShH, orShM, orEhH, orEhM, eodMin/60, eodMin%60,
      uptimeH, uptimeM, _Symbol,
      bal, eq, ddPct,
      g_orHigh, g_orLow<DBL_MAX?g_orLow:0.0, g_orRange, orValid?"YES":"no",
      g_baseRiskPct, g_lastMultiplier, g_lastWasLoss?"ON":"off",
      g_totalTrades, g_wins, g_losses,
      g_totalTrades>0?(g_wins*100.0/g_totalTrades):0.0,
      g_hb_lastReason
   );

   // [TIER PREVIEW] adauga sub status lotul pe fiecare tier (AZI / A DOUA ZI)
   string tierTxt = BuildTierLotText();
   if(StringLen(tierTxt) > 0) text = text + "\n" + tierTxt;

   // Create or update chart label
   if(ObjectFind(0, g_hb_LBL_NAME)<0)
   {
      ObjectCreate(0, g_hb_LBL_NAME, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, g_hb_LBL_NAME, OBJPROP_CORNER, InpHB_Corner);
      ObjectSetInteger(0, g_hb_LBL_NAME, OBJPROP_XDISTANCE, InpHB_PosX);
      ObjectSetInteger(0, g_hb_LBL_NAME, OBJPROP_YDISTANCE, InpHB_PosY);
      ObjectSetInteger(0, g_hb_LBL_NAME, OBJPROP_FONTSIZE, InpHB_FontSize);
      ObjectSetString (0, g_hb_LBL_NAME, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, g_hb_LBL_NAME, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, g_hb_LBL_NAME, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, g_hb_LBL_NAME, OBJPROP_BACK, false);
   }
   ObjectSetString (0, g_hb_LBL_NAME, OBJPROP_TEXT, text);
   ObjectSetInteger(0, g_hb_LBL_NAME, OBJPROP_COLOR, stateColor);

   // Periodic log print to Experts tab
   if(InpHeartbeatLogSec>0 && (now - g_hb_lastLogPrint) >= InpHeartbeatLogSec)
   {
      PrintFormat("[V1I HEARTBEAT] %s | NY=%02d:%02d | Bal=$%.2f DD=%.2f%% | Trades=%d WR=%.1f%% | Reason: %s",
                  state, nyHour, nyMn, bal, ddPct,
                  g_totalTrades, g_totalTrades>0?(g_wins*100.0/g_totalTrades):0.0,
                  g_hb_lastReason);
      g_hb_lastLogPrint = now;
   }
}

//+------------------------------------------------------------------+
//| MAIN TICK — [FIX-3] + [FIX-8] NEW_DAY safe                        |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime now = TimeCurrent();
   string today = NYDateStr(now);
   int nyMin = NYMinOfDay(now);
   int orEnd = InpOREndH*60+InpOREndM;
   int eodMin = InpEODH*60+InpEODM;

   CheckExternalClose();

   // [HEARTBEAT] Afiseaza status pe chart la fiecare tick (low cost)
   {
      int orStartMin = InpORStartH*60+InpORStartM;
      int orEndMin2  = InpOREndH*60+InpOREndM;
      int eodMin2    = InpEODH*60+InpEODM;
      bool hasPos2   = HasMyPosition();
      bool orValid2  = (g_orHigh>0 && g_orLow<DBL_MAX && g_orRange>0);
      V1I_ShowHeartbeat(nyMin, orStartMin, orEndMin2, eodMin2, hasPos2, orValid2, g_traded);
   }

   // [FIX-3 + FIX-8] NEW_DAY safe: close success first, THEN reset state
   if(today != g_lastDay)
   {
      if(HasMyPosition())
      {
         if(!CloseMyPosition("NEW_DAY"))
         {
            // close eșuat — NU reset state, retry pe tick urmator
            Print("[FIX-3] NEW_DAY close FAIL - retry next tick (state pastrat)");
            return;
         }
      }
      // close reusit sau n-am avut pozitie — acum reset
      g_orHigh=0; g_orLow=DBL_MAX; g_orRange=0;
      g_orDone=false; g_traded=false;
      g_tp1Done=false; g_tp2Done=false;
      g_instChase=false; g_instRSIBoost=false;
      g_lastDay = today;
      DeleteAllLines();
      // [INST-4] WEEK RESET intra-sesiune: luni dimineata lantul mart moare
      if(InpMartWeekReset && g_lastWasLoss && g_lastCloseTime > 0 &&
         NYWeekIndex(g_lastCloseTime) < NYWeekIndex(now))
      {
         g_lastWasLoss = false;
         Print("[INST-4 WEEK RESET] saptamana NY noua -> Mart OFF");
      }
   }

   if(HasMyPosition())
   {
      if(nyMin>=eodMin) { CloseMyPosition("EOD"); return; }
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double curPnl = g_isBuy?(bid-g_entryPrice):(g_entryPrice-ask);

      if(!g_tp1Done && InpTP1R>0 && g_riskPoints>0)
      {
         if(curPnl >= g_riskPoints*InpTP1R)
         {
            if(PartialClose(InpTP1Pct, "TP1"))
            {
               g_tp1Done = true;
               MoveSLtoBreakeven();
               DeleteLine("TP1");
               PrintFormat("TP1 HIT %.1fR | inchis %.0f%%", InpTP1R, InpTP1Pct);
            }
         }
      }
      if(g_tp1Done && !g_tp2Done && InpTP2R>0 && g_riskPoints>0)
      {
         if(curPnl >= g_riskPoints*InpTP2R)
         { g_tp2Done = true; CloseMyPosition("TP2"); return; }
      }
      if(InpCapR>0 && g_riskPoints>0)
      {
         if(g_isBuy && bid >= g_entryPrice + g_riskPoints*InpCapR) { CloseMyPosition("CAP"); return; }
         if(!g_isBuy && ask <= g_entryPrice - g_riskPoints*InpCapR) { CloseMyPosition("CAP"); return; }
      }
      if(g_riskPoints>0 && g_orRange>0)
      {
         double sb = GetSpreadBuffer();
         double trailActPts = g_orRange*InpTrailAct;
         if(g_isBuy)
         {
            if(bid > g_trailBest) g_trailBest = bid;
            if(!g_trailActive && (g_trailBest-g_entryPrice) >= trailActPts)
            {
               g_trailActive = true;
               DeleteLine("TRAIL_ACT");
               UpdateTrailLiveLine();
               PrintFormat("TRAIL ON BUY: profit=%.1f >= %.1f", g_trailBest-g_entryPrice, trailActPts);
            }
            if(g_trailActive) TrailSL(g_trailBest - g_riskPoints*InpTrailDist - sb);
         }
         else
         {
            if(ask < g_trailBest) g_trailBest = ask;
            if(!g_trailActive && (g_entryPrice-g_trailBest) >= trailActPts)
            {
               g_trailActive = true;
               DeleteLine("TRAIL_ACT");
               UpdateTrailLiveLine();
               PrintFormat("TRAIL ON SELL: profit=%.1f >= %.1f", g_entryPrice-g_trailBest, trailActPts);
            }
            if(g_trailActive) TrailSL(g_trailBest + g_riskPoints*InpTrailDist + sb);
         }
      }
      return;
   }

   static datetime s_lastBar = 0;
   datetime curBar = iTime(_Symbol, PERIOD_M5, 0);
   if(curBar==0 || curBar==s_lastBar) return;
   s_lastBar = curBar;

   if(g_traded) return;
   if(nyMin<orEnd || nyMin>=eodMin) return;

   if(!g_orDone)
   {
      if(!BuildOR(now)) { g_traded=true; return; }
      double orRng = g_orHigh-g_orLow;
      double atr = GetATR();
      if(atr<=0) { g_traded=true; Print("SKIP: ATR=0"); return; }
      // [MS-1] min OR: absolut in puncte (legacy) sau relativ la ATR D1 (multi-symbol)
      double minOR = (InpScoreMode==SCORE_ATR_NORM) ? atr*InpMinOR_ATR : InpMinOR;
      if(orRng<minOR) { g_traded=true; PrintFormat("SKIP: OR=%.1f<min=%.1f", orRng, minOR); return; }
      double ratio = orRng/atr;
      if(ratio<InpATRLo || ratio>InpATRHi) { g_traded=true; PrintFormat("SKIP: ATR ratio=%.3f", ratio); return; }
      g_orRange = orRng;
      g_lastScore = EvaluateOR(now);
      g_baseRiskPct = ScoreToRisk(g_lastScore);
      g_riskPct = (InpUseMart && g_lastWasLoss) ? MathMin(g_baseRiskPct*InpMartMult, InpRiskCap) : g_baseRiskPct;
      g_orDone = true;

      DrawORLines();

      PrintFormat("OR VALID H=%.2f L=%.2f Range=%.1f Score=%d BaseRisk=%.4f%% -> Risk=%.4f%%%s",
                  g_orHigh, g_orLow, orRng, g_lastScore, g_baseRiskPct, g_riskPct,
                  g_lastWasLoss?StringFormat(" [MART x%.3f]", InpMartMult):"");
   }

   if(g_orDone && !HasMyPosition())
   {
      double pO = iOpen(_Symbol, PERIOD_M5, 1);
      double pC = iClose(_Symbol, PERIOD_M5, 1);
      if(pO==0||pC==0) return;
      bool goL = (pO<=g_orHigh && pC>g_orHigh);
      bool goS = (pO>=g_orLow  && pC<g_orLow);
      if(goL && goS)
      { if((pC-g_orHigh) >= (g_orLow-pC)) goS=false; else goL=false; }
      if(goL)      { g_traded=true; OpenTrade(true,  g_orLow);  }
      else if(goS) { g_traded=true; OpenTrade(false, g_orHigh); }
   }
}
//+------------------------------------------------------------------+
