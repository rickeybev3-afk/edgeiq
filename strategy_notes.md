# EdgeIQ Strategy Notes
Last updated: 2026-04-22

---

## Current Live Config (paper_trader_bot.py — as of 2026-04-22)

### TCS Gates
| Gate | Value | Notes |
|---|---|---|
| Paper min TCS | 49 | Lowest per-structure baseline (Double Distribution); tcs_thresholds.json governs per-structure |
| Live min TCS (real $) | 70 | Hard floor when IS_PAPER_ALPACA=False |
| Morning scan hard floor | 60 | Morning TCS<60 = net negative expectancy, hard blocked |
| PDT priority TCS | 70 | While equity <$25k, only elite-tier setups counted against PDT slots |

### P-Tier Sizing (stacks with IB-range and RVOL mults)
| Tier | Condition | Size Mult | Backtest Stats |
|---|---|---|---|
| P3 | Morning + TCS≥70 | 1.50× | +6.102R/yr avg, 79.7% WR — never miss these |
| P1 | Intraday + TCS≥70 | 1.25× | +3.715R/yr avg, 89.8% WR |
| P2 | Intraday + TCS 50–69 | 1.00× | +1.265R/yr avg, 74.8% WR |
| P4 | Morning + TCS 60–69 | 1.00× | +0.366R/yr avg, 36.9% WR — marginal but +EV |

### IB-Range Size Multiplier
| IB Range | Mult | Rationale |
|---|---|---|
| 0–2% | 1.50× | Tightest structure, highest WR (89.5% / +4.32R avg) |
| 2–4% | 1.30× | Strong (85.3% WR, +1.24R avg) |
| 4–6% | 1.00× | Standard baseline |
| 6–10% | 0.75× | Wider, lower WR |
| ≥10% | BLOCKED | Chaotic structure, WR 54–68% — skip order |

### RVOL Size Multiplier
| RVOL | Mult |
|---|---|
| ≥3.5× | 1.50× |
| ≥2.5× | 1.25× |
| <2.5× | 1.00× |

### Max Combined Exposure
IB mult 2.00× (0–2% IB) × P3 1.50× = **3.00× base risk** hard ceiling (never exceeds this by design).

### Adaptive Exit Targets (from adaptive_exits.json)
Per-band exit targets driven by TCS, scan type, and structure. Example: ACDC 2026-04-22 → TCS 60, morning, Bullish Break → 1.2R target.

### Screener Pass Multiplier
Calibrated from settled paper trades (threshold: 30 trades minimum).

| Pass | Mult | Last calibrated | Notes |
|---|---|---|---|
| gap | 1.00× | baseline anchor | ≥3% gap universe |
| other | 1.15× | (backtest) | <3% daily change |
| trend | 0.85× | (backtest) | 1–3% + above SMA20/50 |
| gap_down | 1.00× | 2026-04-20 | 6 trades settled, n<30 → baseline; re-run once ≥30 settle |
| squeeze | 0.70× | 2026-04-22 (re-calibrated on clean data) | 36 trades, 88.9% WR, +0.009R avg vs gap +0.129R → ratio 0.072 → sqrt-clamp → 0.70× |

**Squeeze calibration history:**
- 2026-04-21 (corrupt): First calibration → 0.70× (32 trades, 96.9% WR, −0.130R avg). Data corrupt: stopped-out trades mislabelled "Win" (BZUN, SCHL, GTM), tiered_pnl_r stored as −1 sentinel (AMPY actual +1.667R), pnl_r_sim used wrong direction for bearish setups.
- 2026-04-22 (clean): Bugs fixed via `python fix_squeeze_data.py --apply` (3 win_loss corrections, 2 tiered_pnl_r corrections). Re-ran `python calibrate_sp_mult.py --pass squeeze --apply`. Result: **36 trades, 88.9% WR, +0.009R avg → 0.70×**. The 0.70× floor is again correct, but now for the right reason: genuine low expectancy vs the gap anchor (+0.009R ÷ +0.129R = 0.072 → sqrt → 0.267 → clamped to 0.70 minimum).

---

## Phase 3 Grid Search Results (2026-04-22 run)

### Best Overall (LOOKAHEAD — NOT deployable live)
- Structure: Neutral Extreme + Neutral + Double Distribution
- Gap direction: up only
- TCS offset: +0 (baseline)
- MFE filter: ≥0.5R **← this is forward-looking — disqualifies for live trading**
- N: 10,389 | WR: 92.2% | Avg R: 1.085R | Sharpe: 7.202 | MaxDD: 5.54R
- Weekly expectancy: 45.94R/wk ($6,891/wk at $150 risk)

> This number is aspirational, not deployable. The MFE≥0.5R filter requires knowing how far price moved in your favor after entry — that's post-hoc information.

### Best Clean (no lookahead, deployable) — PREVIOUSLY DEPLOYED COMBO
- WR: 84.6% | Avg R: 0.929R | PF: 14.9 | Sharpe: 9.85 | MaxDD: 21.59R
- N: 5,266 | Trades per day: 6.5
- This was the Combo #1 used to size/validate the paper bot at inception.
- Bot does NOT auto-switch to new grid results — manual config change required.

---

## Phase 4 Per-Structure Best Combos (2026-04-22 — CLEAN, no lookahead)

These are the real numbers to use for future filter implementation. Run took 1.1 seconds over 191,768 rows / 1,227 trading days.

| Structure | N | WR | Avg R | Sharpe | MaxDD | Key Filters |
|---|---|---|---|---|---|---|
| **Double Distribution** | 101 | **99.0%** | 1.417R | **38.836** | **0.154R** | TCS+15, intraday, gap≥0%, excl false break |
| **Neutral Extreme** | 101 | **96.0%** | 1.448R | **20.886** | 1.00R | TCS+15, morning, RVOL≥1.5, gap≥0%, excl false break |
| **Neutral** | 171 | **93.0%** | 1.162R | **20.351** | 1.333R | TCS+5, intraday, gap≥5%, follow≥1%, excl false break |
| Bullish Break | 148 | 83.8% | 0.516R | 15.778 | 2.276R | TCS+0, any scan, gap≥2%, excl false break |
| Bearish Break | 239 | 75.7% | 0.411R | 13.476 | 3.522R | TCS+0, any scan, gap≥5%, no false break excl |
| Normal Variation | — | — | — | — | — | No qualifying combos (N<100) |
| Non-Trend | — | — | — | — | — | No rows |

**Combined portfolio (union of per-structure best):** N=760, WR=87.0%, Avg R=0.872R, Sharpe=16.456, MaxDD=3.52R

### Raw filter specs per structure (for future implementation):
```
Double Distribution:  TCS_OFFSET=+15  RVOL_MIN=0.0  GAP_MIN=0.0%  SCAN=intraday  EXCL_FB=true
Neutral Extreme:      TCS_OFFSET=+15  RVOL_MIN=1.5  GAP_MIN=0.0%  SCAN=morning   EXCL_FB=true
Neutral:              TCS_OFFSET=+5   RVOL_MIN=0.0  GAP_MIN=5.0%  FOLLOW≥1%      SCAN=intraday  EXCL_FB=true
Bullish Break:        TCS_OFFSET=+0   RVOL_MIN=0.0  GAP_MIN=2.0%  SCAN=any       EXCL_FB=true
Bearish Break:        TCS_OFFSET=+0   RVOL_MIN=0.0  GAP_MIN=5.0%  SCAN=any       EXCL_FB=false
```

---

## Key Files
| File | Purpose |
|---|---|
| `paper_trader_bot.py` | Live bot — P-tier sizing, TCS gates, IB-range mult, adaptive exits |
| `tcs_thresholds.json` | Per-structure TCS minimums (recalibrated nightly) |
| `adaptive_exits.json` | Per-band exit target R by TCS/scan/structure |
| `filter_grid_p4_results.json` | Phase 4 per-structure best combos (overwritten weekly by grid search) |
| `filter_grid_summary.json` | Phase 3 summary — best_combo (clean) + best_combo_with_lookahead |
| `filter_grid_archive/YYYY-MM-DD/` | Weekly archive of Phase 3 run outputs (last N kept) |

---

## Implementation Notes for Phase 4 Filters

When ready to add P4 per-structure filters to the bot:
1. Load `filter_grid_p4_results.json` at bot startup
2. For each setup, look up its structure token against `per_structure_best`
3. Apply structure-specific `tcs_offset` on top of the calibrated per-structure baseline
4. Apply `rvol_min`, `gap_min`, `scan_type`, `excl_false_break` filters before the order gate
5. P-tier sizing and IB-range sizing remain unchanged — they stack on top

The Double Distribution intraday filter (99% WR, Sharpe 38.8, MaxDD 0.15R) is particularly compelling — only 101 trades over 5 years means low frequency but essentially no losses historically. Worth isolating and sizing up when it triggers.

---

## Account Milestones
- Current paper equity: ~$102k (as of 2026-04-22)
- PDT unlock target: $25k (live account — paper is unrestricted)
- Live account start: $7,000
- Risk sizing: 2.1% per trade, $4k hard cap
