"""
pages/filter_sim.py
────────────────────
Interactive filter simulation dashboard.
Lets you dial TCS minimum, IB range % ceiling, and VWAP toggle to see
live WR / expectancy changes against the full historical breakout dataset.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backend

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="EdgeIQ · Filter Simulation",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Default VWAP to OFF (backtest: no edge) but allow user to toggle on manually
if "fs_use_vwap" not in st.session_state:
    st.session_state["fs_use_vwap"] = False

st.markdown("""
<style>
  [data-testid="stSidebar"] { display: none; }
  .block-container { padding-top: 1.5rem; padding-bottom: 3rem; }
  h1, h2 { color: #7986cb; }
  h3 { color: #9fa8da; }

  /* Metric cards */
  .filter-card {
    background: #12122a;
    border: 1px solid #2d2d5e;
    border-radius: 10px;
    padding: 18px 20px;
    text-align: center;
    height: 100%;
  }
  .filter-card.active {
    border-color: #5c6bc0;
    background: #16163a;
  }
  .filter-card .label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: #9fa8da;
    margin-bottom: 6px;
  }
  .filter-card .wr {
    font-size: 36px;
    font-weight: 700;
    color: #80cbc4;
    line-height: 1.1;
  }
  .filter-card .sub {
    font-size: 13px;
    color: #7986cb;
    margin-top: 4px;
  }
  .filter-card .n {
    font-size: 12px;
    color: #546e7a;
    margin-top: 2px;
  }

  /* Funnel arrow row */
  .funnel-arrow {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 6px 0;
  }
  .funnel-arrow span {
    font-size: 22px;
    color: #37474f;
  }

  /* Rejected badge */
  .reject-badge {
    background: #1a0000;
    border: 1px solid #b71c1c;
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 13px;
    color: #ef9a9a;
    margin: 4px 0;
  }

  /* Gate indicator */
  .gate-ok   { color: #66bb6a; font-weight: 700; }
  .gate-warn { color: #ffee58; font-weight: 700; }
  .gate-fail { color: #ef9a9a; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

SUPABASE     = backend.supabase
USER_ID      = "a5e1fcab-8369-42c4-8550-a8a19734510c"
PAGE_SIZE    = 1000
MAX_ROWS     = 0  # 0 = full history

# ── Data loader (cached per session) ──────────────────────────────────────────
@st.cache_data(show_spinner="Loading historical breakout data…", ttl=3600)
def load_breakout_rows(user_id: str, max_rows: int = 0):
    cols = (
        "actual_outcome,tcs,ib_range_pct,close_vs_vwap_pct,"
        "pnl_r_sim,scan_type,sim_date,rvol"
    )
    all_rows = []
    offset   = 0
    while True:
        page = PAGE_SIZE if max_rows == 0 else min(PAGE_SIZE, max_rows - len(all_rows))
        if page <= 0:
            break
        last_exc = None
        for _attempt in range(3):
            try:
                resp = (
                    SUPABASE.table("backtest_sim_runs")
                    .select(cols)
                    .eq("user_id", user_id)
                    .eq("actual_outcome", "Bullish Break")
                    .not_.is_("pnl_r_sim", "null")
                    .range(offset, offset + page - 1)
                    .execute()
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(1.5 * (_attempt + 1))
        if last_exc is not None:
            raise last_exc
        batch = resp.data or []
        all_rows.extend(batch)
        if len(batch) < page or (max_rows > 0 and len(all_rows) >= max_rows):
            break
        offset += page
    return all_rows


# ── Stats helper ──────────────────────────────────────────────────────────────
def compute_stats(rows):
    if not rows:
        return {"n": 0, "wr": 0.0, "exp": 0.0, "total_r": 0.0, "avg_win": None, "avg_loss": None}
    pnls   = [r["pnl_r_sim"] for r in rows]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n      = len(pnls)
    return {
        "n":        n,
        "wr":       len(wins) / n * 100,
        "exp":      sum(pnls) / n,
        "total_r":  sum(pnls),
        "avg_win":  sum(wins)   / len(wins)   if wins   else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
    }


# ── VWAP alignment helper ─────────────────────────────────────────────────────
def vwap_aligned(row):
    cvv = row.get("close_vs_vwap_pct")
    if cvv is None:
        return True  # missing data → pass through
    outcome = row.get("actual_outcome", "")
    if outcome == "Bullish Break":
        return cvv >= 0.0
    return cvv <= 0.0


# ── Money formatter for large numbers ─────────────────────────────────────────
def fmt_money(v):
    """Format a dollar amount compactly: $1.2T / $43B / $1.2M / $42,000"""
    av = abs(v)
    sign = "-" if v < 0 else ""
    if av >= 1e12:
        return f"{sign}${av/1e12:.2f}T"
    if av >= 1e9:
        return f"{sign}${av/1e9:.2f}B"
    if av >= 1e6:
        return f"{sign}${av/1e6:.2f}M"
    return f"{sign}${av:,.0f}"


# ── WR color helper ───────────────────────────────────────────────────────────
def wr_color(wr):
    if wr >= 88:
        return "#66bb6a"
    if wr >= 70:
        return "#80cbc4"
    if wr >= 55:
        return "#ffee58"
    return "#ef9a9a"


# ── Card renderer ─────────────────────────────────────────────────────────────
def stat_card(label, s, active=False):
    wr   = s["wr"]
    exp  = s["exp"]
    n    = s["n"]
    color = wr_color(wr)
    active_cls = "active" if active else ""
    st.markdown(f"""
    <div class="filter-card {active_cls}">
      <div class="label">{label}</div>
      <div class="wr" style="color:{color};">{wr:.1f}%</div>
      <div class="sub">{exp:+.3f}R / trade</div>
      <div class="n">n = {n:,}</div>
    </div>
    """, unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# RENDER
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("# 🔬 Filter Simulation")
st.caption(
    "Dial the filters below and see instantly how Win Rate and expectancy change "
    "across your full historical breakout dataset. "
    "The rightmost card mirrors your live bot settings."
)

# ── Load data ─────────────────────────────────────────────────────────────────
try:
    all_rows = load_breakout_rows(USER_ID, MAX_ROWS)
except Exception as _load_err:
    st.error(
        f"⚠️ Could not load backtest data — database connection dropped. "
        f"Refresh the page to retry. (Detail: {type(_load_err).__name__})"
    )
    st.stop()
if not all_rows:
    st.error("No historical breakout data found. Run a batch backtest first.")
    st.stop()

# ── Sidebar / controls ────────────────────────────────────────────────────────
st.markdown("### Controls")
ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([2, 2, 1.5, 1.5])

with ctrl_col1:
    tcs_min = st.slider(
        "Min TCS score", min_value=30, max_value=85, value=50, step=5,
        help="Trade Conviction Score. Bot floors are enforced automatically: morning ≥60, intraday ≥50. Slider raises the floor further above those minimums."
    )

with ctrl_col2:
    ib_max = st.slider(
        "Max IB range %", min_value=3.0, max_value=25.0, value=10.0, step=0.5,
        help="Initial Balance range as % of open price. Wide IBs = chaotic structure. 10% is the standard ceiling; lower = tighter, calmer setups."
    )

with ctrl_col3:
    use_vwap = st.toggle("Require VWAP side", key="fs_use_vwap",
                         help="Only take Bullish Breaks where close was above VWAP. Removes setups fighting the intraday trend. No edge confirmed in backtest — off by default.")

with ctrl_col4:
    scan_type_filter = st.selectbox("Scan type", ["All", "morning", "intraday"],
                                    help="Filter to only morning (pre-10:30) or intraday scans, or show all.")

st.divider()

# ── Filter rows ───────────────────────────────────────────────────────────────
# Apply scan type filter globally if selected
src = all_rows if scan_type_filter == "All" else [
    r for r in all_rows if r.get("scan_type") == scan_type_filter
]

# Layer 0: Bullish Break only (Bearish Break excluded at query level — bot always skips)
s0 = compute_stats(src)

# Layer 1: TCS — bot split floors: morning/null ≥60, intraday ≥50 (slider raises further)
def _tcs_floor(r, slider_min):
    scan = (r.get("scan_type") or "morning").lower()
    bot_floor = 50 if scan.startswith("intraday") else 60
    return max(slider_min, bot_floor)

tcs_pass = [r for r in src if (r.get("tcs") or 0) >= _tcs_floor(r, tcs_min)]
s1 = compute_stats(tcs_pass)

# Layer 2: + IB range
has_ib    = [r for r in tcs_pass if r.get("ib_range_pct") is not None]
ib_pass   = [r for r in has_ib   if r["ib_range_pct"] < ib_max]
ib_wide   = [r for r in has_ib   if r["ib_range_pct"] >= ib_max]
no_ib     = [r for r in tcs_pass if r.get("ib_range_pct") is None]
after_ib  = ib_pass + no_ib
s2 = compute_stats(after_ib)

# Layer 3: + RVOL ≥ 1.0 (bot filter — skip when data present and < 1.0; NULL passes)
rvol_rej  = [r for r in after_ib if r.get("rvol") is not None and float(r.get("rvol") or 0) < 1.0]
after_rvol = [r for r in after_ib if r.get("rvol") is None or float(r.get("rvol") or 0) >= 1.0]
s_rvol = compute_stats(after_rvol)

# Layer 4: + VWAP
if use_vwap:
    final       = [r for r in after_rvol if vwap_aligned(r)]
    vwap_rej    = [r for r in after_rvol if not vwap_aligned(r)]
else:
    final       = after_rvol
    vwap_rej    = []
s3 = compute_stats(final)

# ── Filter funnel cards ───────────────────────────────────────────────────────
st.markdown("### Filter Funnel")
fc0, fca, fc1, fcb, fc2, fcc, fc_rvol, fcd, fc3 = st.columns([3, 0.4, 3, 0.4, 3, 0.4, 3, 0.4, 3])

with fc0:
    stat_card("Bullish Break Signals", s0)
with fca:
    st.markdown('<div class="funnel-arrow"><span>▶</span></div>', unsafe_allow_html=True)
with fc1:
    stat_card(f"TCS ≥ {tcs_min} (bot floors: 60m/50i)", s1, active=True)
with fcb:
    st.markdown('<div class="funnel-arrow"><span>▶</span></div>', unsafe_allow_html=True)
with fc2:
    stat_card(f"+ IB < {ib_max:.1f}%", s2, active=True)
with fcc:
    st.markdown('<div class="funnel-arrow"><span>▶</span></div>', unsafe_allow_html=True)
with fc_rvol:
    stat_card("+ RVOL ≥ 1.0", s_rvol, active=True)
with fcd:
    st.markdown('<div class="funnel-arrow"><span>▶</span></div>', unsafe_allow_html=True)
with fc3:
    stat_card(f"{'+ VWAP aligned' if use_vwap else '(VWAP off)'} — Live Settings", s3, active=True)

# ── Rejection summary ─────────────────────────────────────────────────────────
rj1 = s0["n"] - s1["n"]
rj2 = s1["n"] - s2["n"]
rj_rvol = s2["n"] - s_rvol["n"]
rj3 = s_rvol["n"] - s3["n"]

st.markdown("&nbsp;")
rj_col1, rj_col2, rj_col_rvol, rj_col3 = st.columns(4)
with rj_col1:
    if rj1 > 0:
        st.markdown(
            f'<div class="reject-badge">✂ TCS filter removes <b>{rj1:,}</b> trades '
            f'({rj1/s0["n"]*100:.0f}%)</div>', unsafe_allow_html=True
        )
with rj_col2:
    if rj2 > 0 and has_ib:
        ib_wide_stats = compute_stats(ib_wide)
        st.markdown(
            f'<div class="reject-badge">✂ IB filter removes <b>{rj2:,}</b> trades '
            f'({rj2/s1["n"]*100:.0f}%) — those had <b>{ib_wide_stats["wr"]:.0f}%</b> WR</div>',
            unsafe_allow_html=True
        )
with rj_col_rvol:
    if rj_rvol > 0:
        rvol_rej_stats = compute_stats(rvol_rej)
        st.markdown(
            f'<div class="reject-badge">✂ RVOL filter removes <b>{rj_rvol:,}</b> trades '
            f'({rj_rvol/s2["n"]*100:.0f}%) — those had <b>{rvol_rej_stats["wr"]:.0f}%</b> WR</div>',
            unsafe_allow_html=True
        )
with rj_col3:
    if rj3 > 0 and use_vwap:
        vwap_rej_stats = compute_stats(vwap_rej)
        st.markdown(
            f'<div class="reject-badge">✂ VWAP filter removes <b>{rj3:,}</b> trades '
            f'({rj3/s_rvol["n"]*100:.0f}%) — those had <b>{vwap_rej_stats["wr"]:.0f}%</b> WR</div>',
            unsafe_allow_html=True
        )

st.divider()

# ── Charts row ────────────────────────────────────────────────────────────────
chart_left, chart_right = st.columns(2)

# Left: WR waterfall ──────────────────────────────────────────────────────────
with chart_left:
    st.markdown("#### Win-Rate by Filter Layer")
    layers_labels = [
        "Bullish Breaks",
        f"TCS ≥ {tcs_min}",
        f"+ IB < {ib_max:.1f}%",
        "+ RVOL ≥ 1.0",
        f"{'+ VWAP' if use_vwap else '(no VWAP)'} [Live]",
    ]
    layers_wr  = [s0["wr"], s1["wr"], s2["wr"], s_rvol["wr"], s3["wr"]]
    layers_exp = [s0["exp"], s1["exp"], s2["exp"], s_rvol["exp"], s3["exp"]]
    bar_colors = [wr_color(w) for w in layers_wr]

    fig_wr = go.Figure()
    fig_wr.add_trace(go.Bar(
        x=layers_labels,
        y=layers_wr,
        marker_color=bar_colors,
        text=[f"{w:.1f}%" for w in layers_wr],
        textposition="outside",
        name="Win Rate",
    ))
    fig_wr.update_layout(
        plot_bgcolor="#0e0e1e",
        paper_bgcolor="#0e0e1e",
        font=dict(color="#9fa8da"),
        yaxis=dict(title="Win Rate (%)", range=[0, 105], gridcolor="#1e1e3a"),
        xaxis=dict(gridcolor="#1e1e3a"),
        margin=dict(t=20, b=20, l=10, r=10),
        height=280,
        showlegend=False,
    )
    st.plotly_chart(fig_wr, use_container_width=True)

# Right: Expectancy waterfall ─────────────────────────────────────────────────
with chart_right:
    st.markdown("#### Expectancy (avg R/trade) by Filter Layer")
    exp_colors = ["#80cbc4" if e >= 0 else "#ef9a9a" for e in layers_exp]
    fig_exp = go.Figure()
    fig_exp.add_trace(go.Bar(
        x=layers_labels,
        y=layers_exp,
        marker_color=exp_colors,
        text=[f"{e:+.3f}R" for e in layers_exp],
        textposition="outside",
        name="Expectancy",
    ))
    fig_exp.update_layout(
        plot_bgcolor="#0e0e1e",
        paper_bgcolor="#0e0e1e",
        font=dict(color="#9fa8da"),
        yaxis=dict(title="Avg R / trade", gridcolor="#1e1e3a"),
        xaxis=dict(gridcolor="#1e1e3a"),
        margin=dict(t=20, b=20, l=10, r=10),
        height=280,
        showlegend=False,
    )
    st.plotly_chart(fig_exp, use_container_width=True)

st.divider()

# ── IB range bucket breakdown ─────────────────────────────────────────────────
st.markdown("#### WR by IB Range Bucket (TCS-filtered universe)")
tcs_with_ib = [r for r in tcs_pass if r.get("ib_range_pct") is not None]
if tcs_with_ib:
    buckets = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10), (10, 15), (15, 999)]
    bucket_labels, bucket_wrs, bucket_exps, bucket_ns, bucket_colors = [], [], [], [], []
    for lo, hi in buckets:
        b = [r for r in tcs_with_ib if lo <= r["ib_range_pct"] < hi]
        if not b:
            continue
        bs = compute_stats(b)
        label = f"{lo}–{hi}%" if hi < 900 else f"{lo}%+"
        bucket_labels.append(label)
        bucket_wrs.append(bs["wr"])
        bucket_exps.append(bs["exp"])
        bucket_ns.append(bs["n"])
        bucket_colors.append(wr_color(bs["wr"]))

    if bucket_labels:
        bc_left, bc_right = st.columns(2)
        with bc_left:
            fig_bucket = go.Figure()
            fig_bucket.add_trace(go.Bar(
                x=bucket_labels,
                y=bucket_wrs,
                marker_color=bucket_colors,
                text=[f"{w:.0f}%<br>n={n}" for w, n in zip(bucket_wrs, bucket_ns)],
                textposition="outside",
            ))
            # Add cutoff line (only if the exact bucket label exists)
            _cutoff_label = f"0–{int(ib_max)}%"
            if _cutoff_label in bucket_labels:
                fig_bucket.add_vline(
                    x=bucket_labels.index(_cutoff_label),
                    line_dash="dash", line_color="#ef9a9a",
                    annotation_text=f"Cutoff: {ib_max:.1f}%", annotation_font_color="#ef9a9a",
                )
            fig_bucket.update_layout(
                plot_bgcolor="#0e0e1e", paper_bgcolor="#0e0e1e",
                font=dict(color="#9fa8da"),
                yaxis=dict(title="Win Rate (%)", range=[0, 110], gridcolor="#1e1e3a"),
                xaxis=dict(title="IB Range %", gridcolor="#1e1e3a"),
                margin=dict(t=10, b=20, l=10, r=10),
                height=270, showlegend=False,
            )
            st.plotly_chart(fig_bucket, use_container_width=True)

        with bc_right:
            # Table summary
            bucket_df = pd.DataFrame({
                "IB Range":    bucket_labels,
                "WR %":        [f"{w:.1f}%" for w in bucket_wrs],
                "Exp (R)":     [f"{e:+.3f}" for e in bucket_exps],
                "n":           bucket_ns,
                "Passes filter": ["✅" if lo < ib_max else "❌"
                                  for lo, _ in buckets[:len(bucket_labels)]],
            })
            st.dataframe(bucket_df, use_container_width=True, hide_index=True)
else:
    st.info("No IB range data available in this subset.")

st.divider()

# ── Scan type breakdown ───────────────────────────────────────────────────────
st.markdown("#### Live-Filter Trades by Scan Type")
sc_cols = st.columns(2)
for i, stype in enumerate(["morning", "intraday"]):
    subset = [r for r in final if r.get("scan_type") == stype]
    ss = compute_stats(subset)
    with sc_cols[i]:
        color = wr_color(ss["wr"]) if ss["n"] else "#546e7a"
        st.markdown(f"""
        <div class="filter-card active" style="text-align:left; padding:16px 20px;">
          <div class="label">{stype.upper()}</div>
          <div style="font-size:28px; font-weight:700; color:{color};">{ss["wr"]:.1f}% WR</div>
          <div style="font-size:13px; color:#7986cb; margin-top:4px;">{ss["exp"]:+.3f}R / trade</div>
          <div style="font-size:12px; color:#546e7a;">n = {ss["n"]:,}  ·  total {ss["total_r"]:+.1f}R</div>
        </div>
        """, unsafe_allow_html=True)

st.divider()

# ── Full-filter summary table ─────────────────────────────────────────────────
st.markdown("#### Summary — All Filter Combinations")
summary_data = []
for tcs_val in [40, 50, 60, 70]:
    tcs_rows = [r for r in src if (r.get("tcs") or 0) >= tcs_val]
    for ib_val in [8.0, 10.0, 12.0, 15.0]:
        has_ib_rows = [r for r in tcs_rows if r.get("ib_range_pct") is not None]
        ib_rows     = [r for r in has_ib_rows if r["ib_range_pct"] < ib_val] + [
            r for r in tcs_rows if r.get("ib_range_pct") is None
        ]
        vwap_rows   = [r for r in ib_rows if vwap_aligned(r)]
        sv = compute_stats(vwap_rows)
        if sv["n"] > 0:
            is_current = (tcs_val == tcs_min and ib_val == ib_max)
            summary_data.append({
                "TCS >=":    tcs_val,
                "IB < %":    ib_val,
                "VWAP":      "✅",
                "WR %":      round(sv["wr"], 1),
                "Exp R":     round(sv["exp"], 3),
                "n":         sv["n"],
                "":          "▶ current" if is_current else "",
            })

if summary_data:
    df_summary = pd.DataFrame(summary_data)
    st.dataframe(
        df_summary.style.applymap(
            lambda v: "color: #66bb6a; font-weight:700;" if v == "▶ current" else "",
            subset=[""]
        ),
        use_container_width=True,
        hide_index=True,
        height=280,
    )

st.divider()

# ── Projected P&L ─────────────────────────────────────────────────────────────
st.markdown("### 💰 Projected P&L — Current Filter Settings")
st.caption(
    f"Simulates your account taking the top-TCS trades each day from the "
    f"**{s3['n']:,}-signal live-filter set**. Use 'Max trades / day' below to set your concurrency."
)

# ── Bot Mode toggle ────────────────────────────────────────────────────────────
_bm_col, _bm_info = st.columns([1, 3])
with _bm_col:
    bot_mode = st.toggle(
        "🤖 Live Bot Mode",
        value=True,
        key="fs_bot_mode",
        help=(
            "When ON: uses the exact live-bot sizing rules — 2.1% of equity per trade, "
            "$4,000 hard cap, and P-tier multipliers (P3 morning TCS≥70 → 1.50×, "
            "P1 intraday TCS≥70 → 1.25×, P2 baseline → 1.00×). "
            "When OFF: use the manual sizing controls below."
        ),
    )
with _bm_info:
    if bot_mode:
        st.info(
            "**Bot Mode ON** — 2.1% of equity / trade · $4,000 hard cap · "
            "P3 morning TCS≥70 → 1.50× · P1 intraday TCS≥70 → 1.25× · P2 → 1.00×  "
            "*(matches live paper_trader_bot.py exactly)*  \n"
            "📊 Bot uses **per-structure TCS floors** (49–70) from Phase 3 baseline, not a blanket minimum. "
            "Paper mode: `double_dist` ≥49 · `ntrl_extreme` ≥53 · `bullish/bearish break` ≥57 · `neutral` ≥60+. "
            "Live mode: TCS≥70 across all structures."
        )

# ── Primary sizing inputs (plain English) ─────────────────────────────────────
_r1c1, _r1c2, _r1c3, _r1c4 = st.columns([1.2, 1.2, 1, 1])
with _r1c1:
    pnl_equity = st.number_input(
        "Account size ($)", min_value=1000, max_value=500_000,
        value=7000, step=500, key="fs_pnl_equity",
        help="Your total trading account balance.",
    )
with _r1c2:
    pnl_pos_size = st.number_input(
        "Trade size ($)", min_value=100, max_value=500_000,
        value=1500, step=100, key="fs_pnl_pos_size",
        help="How much you put into each trade (e.g. $1,500).",
    )
with _r1c3:
    pnl_stop_pct = st.number_input(
        "Stop loss %", min_value=1.0, max_value=50.0,
        value=10.0, step=0.5, key="fs_pnl_stop_pct",
        help="How far your stop is from entry, as a % of trade size. "
             "10% stop on a $1,500 trade = $150 max loss.",
    )
with _r1c4:
    pnl_fixed_risk = round(float(pnl_pos_size) * float(pnl_stop_pct) / 100.0, 2)
    st.metric("Max loss per trade", f"${pnl_fixed_risk:,.0f}",
              help="This is your 1R — the most you lose on a stopped-out trade.")

pnl_pos_compound = st.checkbox(
    "📈 Grow trade size as account grows",
    value=False, key="fs_pnl_pos_compound",
    help="When checked: if your account doubles, your trade size doubles too "
         "(capped at 20× to keep numbers realistic). "
         "When unchecked: always the same trade size regardless of P&L.",
)

pnl_risk_mode = "Fixed position size ($)"
pnl_risk_pct  = None

with st.expander("⚙️ Advanced sizing"):
    _adv_mode = st.radio(
        "Override with",
        ["Use settings above", "% of equity per trade (pure compounding)"],
        horizontal=True, key="fs_pnl_adv_mode",
    )
    if _adv_mode == "% of equity per trade (pure compounding)":
        pnl_risk_pct  = st.number_input(
            "Risk % per trade", min_value=0.5, max_value=10.0,
            value=2.1, step=0.1, key="fs_pnl_risk_pct",
            help="Each trade risks this % of your current account balance. "
                 "Grows automatically as your account grows.",
        )
        pnl_fixed_risk   = None
        pnl_pos_compound = False
        st.caption(f"At ${pnl_equity:,} account → 1R = "
                   f"**${pnl_equity * pnl_risk_pct / 100:,.0f}** to start")

# ── Date range filter ──────────────────────────────────────────────────────────
_all_sorted = sorted(final, key=lambda r: r.get("sim_date") or "")
_all_sim_dates = [r["sim_date"] for r in _all_sorted if r.get("sim_date")]
_data_min = datetime.strptime(_all_sim_dates[0],  "%Y-%m-%d").date() if _all_sim_dates else datetime(2021, 6, 1).date()
_data_max = datetime.strptime(_all_sim_dates[-1], "%Y-%m-%d").date() if _all_sim_dates else datetime.today().date()

# ── Auto-compute avg qualifying signals per trading day from current filter set ─
# Use after_ib (TCS + IB), weekdays only, last 60 trading days only so the
# default reflects the CURRENT scanner universe, not diluted multi-year history.
_fs_day_counts: dict = {}
for _r in after_ib:
    _d = (_r.get("sim_date") or "")[:10]
    if _d:
        try:
            _dow = datetime.strptime(_d, "%Y-%m-%d").weekday()  # 0=Mon … 6=Sun
        except Exception:
            continue
        if _dow >= 5:
            continue
        _fs_day_counts[_d] = _fs_day_counts.get(_d, 0) + 1
# Restrict to most recent 60 trading days so average reflects current watchlist
_fs_recent_days = sorted(_fs_day_counts.keys())[-60:]
_fs_recent_counts = [_fs_day_counts[_d] for _d in _fs_recent_days]
_avg_tpd_auto = max(1, round(sum(_fs_recent_counts) / len(_fs_recent_counts))) if _fs_recent_counts else 20

# value=_avg_tpd_auto is only used by Streamlit on truly fresh sessions
# (when the key is absent from session_state). Existing user values are preserved.

_dr_c1, _dr_c2, _dr_c3 = st.columns([1, 1, 0.8])
with _dr_c1:
    pnl_date_start = st.date_input(
        "Sim start date", value=_data_min, min_value=_data_min, max_value=_data_max,
        key="fs_pnl_date_start",
        help="Only include trades on or after this date in the P&L simulation.",
    )
with _dr_c2:
    pnl_date_end = st.date_input(
        "Sim end date", value=_data_max, min_value=_data_min, max_value=_data_max,
        key="fs_pnl_date_end",
        help="Only include trades on or before this date in the P&L simulation.",
    )
with _dr_c3:
    pnl_max_per_day = st.number_input(
        "Max trades / day", min_value=1, max_value=100, value=_avg_tpd_auto, step=1,
        key="fs_pnl_max_per_day",
        help="Auto-set to the average qualifying signals per trading day from your "
             "current filter settings. Caps how many trades are taken per day — "
             "highest TCS first. Adjust to model different concurrency.",
    )

_fs_ib_total   = len(after_ib)
_fs_ib_days    = len(_fs_day_counts)
_fs_ib_avg_raw = round(_fs_ib_total / _fs_ib_days, 1) if _fs_ib_days else 0
st.caption(
    f"ℹ️ The full dataset has **{s3['n']:,} qualifying signals** across all tickers — "
    f"but your live account takes at most **{pnl_max_per_day}/day** "
    f"(highest TCS first). Adjust 'Max trades / day' to model different concurrency.  "
    f"*Auto-default: {_fs_ib_total:,} TCS+IB signals ÷ {_fs_ib_days:,} trading days = {_fs_ib_avg_raw}/day avg.*"
)

if s3["n"] == 0:
    st.info("No trades pass the current filters. Adjust sliders above.")
else:
    # ── Sort and extract date range ────────────────────────────────────────────
    _date_filtered = [
        r for r in _all_sorted
        if r.get("sim_date")
        and str(pnl_date_start) <= r["sim_date"] <= str(pnl_date_end)
    ]
    # ── Per-day cap: take top N by TCS per trading day ──────────────────────
    # Group by the date portion only (first 10 chars) so datetime strings
    # with time components don't create false unique keys per row.
    if pnl_max_per_day and pnl_max_per_day > 0:
        from collections import defaultdict as _dd
        _by_date: dict = _dd(list)
        for _r in _date_filtered:
            _day_key = (_r.get("sim_date") or "")[:10]
            if _day_key:
                _by_date[_day_key].append(_r)
        sorted_trades = []
        for _d in sorted(_by_date.keys()):
            _day_trades = sorted(_by_date[_d], key=lambda x: float(x.get("tcs") or 0), reverse=True)
            sorted_trades.extend(_day_trades[:pnl_max_per_day])
    else:
        sorted_trades = _date_filtered
    if not sorted_trades:
        st.info("No trades fall within the selected date range. Adjust the sim start/end dates above.")
        st.stop()
    sim_dates = [r["sim_date"] for r in sorted_trades if r.get("sim_date")]
    sim_start_str = sim_dates[0]  if sim_dates else None
    sim_end_str   = sim_dates[-1] if sim_dates else None
    trading_years = None
    if sim_start_str and sim_end_str:
        try:
            d1 = datetime.strptime(sim_start_str, "%Y-%m-%d")
            d2 = datetime.strptime(sim_end_str,   "%Y-%m-%d")
            trading_years = max((d2 - d1).days / 365.25, 1/365.25)
        except Exception:
            pass

    # ── Risk % warning ─────────────────────────────────────────────────────────
    if pnl_fixed_risk is not None:
        _risk_pct_warn = float(pnl_fixed_risk) / float(pnl_equity) * 100
        if _risk_pct_warn > 10:
            st.warning(
                f"⚠️ **${pnl_fixed_risk:,} risk on a ${pnl_equity:,} account = "
                f"{_risk_pct_warn:.1f}% per trade.** Standard practice is 1–2%. "
                "Results below will look unrealistically large."
            )

    # ── Simulation ─────────────────────────────────────────────────────────────
    _start_eq = float(pnl_equity)
    COMPOUND_CAP   = 20.0   # used only in non-bot-mode paths
    BOT_RISK_PCT   = 0.021  # 2.1% of equity (live bot)
    BOT_RISK_CAP   = 4000.0 # $4,000 hard cap (matches 20× projection model ceiling)
    # P-tier multipliers (live bot — paper_trader_bot.py)
    PTIER_MORNING_HIGH = 1.50  # P3: morning + TCS ≥ 70
    PTIER_INTRA_HIGH   = 1.25  # P1: intraday + TCS ≥ 70
    PTIER_BASELINE     = 1.00  # P2/P4: everything else
    PTIER_TCS_FLOOR    = 70.0

    eq   = _start_eq
    curve = [eq]
    peak  = eq
    max_dd_pct    = 0.0
    max_dd_dollar = 0.0
    drawdown_pcts = [0.0]
    cap_hit = False
    ptier_counts  = {"P3": 0, "P1": 0, "P2": 0}

    for trade in sorted_trades:
        r_val = trade.get("pnl_r_sim") or 0.0
        if bot_mode:
            # ── Live bot: 2.1% of current equity, $4k hard cap, P-tier mult ──
            base_risk = min(eq * BOT_RISK_PCT, BOT_RISK_CAP)
            if base_risk >= BOT_RISK_CAP:
                cap_hit = True
            tcs_val   = float(trade.get("tcs") or 50)
            scan      = (trade.get("scan_type") or "").lower()
            is_morning = "morning" in scan or scan == "gap"
            if tcs_val >= PTIER_TCS_FLOOR and is_morning:
                ptier_mult = PTIER_MORNING_HIGH
                ptier_counts["P3"] += 1
            elif tcs_val >= PTIER_TCS_FLOOR:
                ptier_mult = PTIER_INTRA_HIGH
                ptier_counts["P1"] += 1
            else:
                ptier_mult = PTIER_BASELINE
                ptier_counts["P2"] += 1
            risk_amt = base_risk * ptier_mult
        elif pnl_fixed_risk is not None:
            if pnl_pos_compound:
                compound_factor = min(eq / _start_eq, COMPOUND_CAP)
                if compound_factor >= COMPOUND_CAP:
                    cap_hit = True
                risk_amt = float(pnl_fixed_risk) * compound_factor
            else:
                risk_amt = float(pnl_fixed_risk)
        else:
            # % of equity compounding
            compound_factor = min(eq / _start_eq, COMPOUND_CAP)
            if compound_factor >= COMPOUND_CAP:
                cap_hit = True
            base_risk = _start_eq * (float(pnl_risk_pct) / 100.0)
            risk_amt  = base_risk * compound_factor
        eq += r_val * risk_amt
        eq  = max(eq, 0.0)
        curve.append(eq)
        if eq > peak:
            peak = eq
        dd_dollar = peak - eq
        dd_pct    = dd_dollar / peak * 100 if peak > 0 else 0.0
        drawdown_pcts.append(dd_pct)
        if dd_dollar > max_dd_dollar:
            max_dd_dollar = dd_dollar
            max_dd_pct    = dd_pct

    final_equity   = curve[-1]
    net_return_pct = (final_equity - float(pnl_equity)) / float(pnl_equity) * 100
    _sim_n         = len(sorted_trades)
    avg_trade_amt  = (final_equity - float(pnl_equity)) / _sim_n if _sim_n else 0

    # Annualised return (CAGR) — skip if period too short to annualise meaningfully
    ann_return = None
    if trading_years and trading_years >= 0.25 and float(pnl_equity) > 0 and final_equity > 0:
        try:
            _raw_cagr = ((final_equity / float(pnl_equity)) ** (1.0 / trading_years) - 1) * 100
            ann_return = _raw_cagr if _raw_cagr <= 99_999 else None  # cap at 99,999%
        except (OverflowError, ZeroDivisionError, ValueError):
            ann_return = None

    trades_per_yr = _sim_n / trading_years if trading_years else None

    # ── Period header ──────────────────────────────────────────────────────────
    if sim_start_str and sim_end_str:
        period_str = f"{sim_start_str} → {sim_end_str}"
        yrs_str    = f"{trading_years:.1f} yr" if trading_years else ""
        tpy_str    = f"{trades_per_yr:.0f} trades/yr" if trades_per_yr else ""
        st.markdown(
            f"<div style='color:#7986cb;font-size:13px;margin-bottom:8px;'>"
            f"📅 Simulation period: <b>{period_str}</b> &nbsp;·&nbsp; "
            f"{yrs_str} &nbsp;·&nbsp; {tpy_str}"
            f"</div>",
            unsafe_allow_html=True,
        )

    # ── Cap-hit notice ─────────────────────────────────────────────────────────
    if bot_mode and cap_hit:
        st.info(
            "ℹ️ **$4,000/trade cap reached** — bot stopped scaling risk once "
            "account equity crossed ~**$190,000** (2.1% × $190k = $4k). "
            "Above that, gains still compound but the per-trade dollar risk stays flat at $4k × P-tier."
        )
    elif cap_hit:
        st.info(
            f"ℹ️ Position size capped at **{int(COMPOUND_CAP)}× starting risk** "
            f"(same guardrail used in the main backtest engine). "
            "Without the cap, compounding at this WR over thousands of trades "
            "produces unrealistic astronomical numbers."
        )

    # ── Summary metrics ────────────────────────────────────────────────────────
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Final Equity",    fmt_money(final_equity),
              delta=f"{net_return_pct:+.1f}% total")
    m2.metric("CAGR (annualised)",
              f"{ann_return:+.1f}%" if ann_return is not None else "n/a (< 3 months)",
              delta=f"over {trading_years:.1f} yrs" if trading_years else None)
    m3.metric("Max Drawdown",    fmt_money(max_dd_dollar),
              delta=f"{max_dd_pct:.1f}% from peak")
    m4.metric("Avg $ / Trade",   fmt_money(avg_trade_amt),
              delta=f"{s3['exp']:+.3f}R avg")
    if bot_mode:
        _r_label = "2.1% of equity · $4k cap"
        _r_delta  = "P-tier mult applied"
    elif pnl_fixed_risk is not None:
        _compound_note = " (compounding)" if pnl_pos_compound else ""
        _r_label = f"1R = {fmt_money(pnl_fixed_risk)}{_compound_note}"
        _r_delta  = None
    elif cap_hit:
        _r_label = f"1R = {pnl_risk_pct}% of equity (capped {int(COMPOUND_CAP)}×)"
        _r_delta  = None
    else:
        _r_label = f"1R = {pnl_risk_pct}% of equity"
        _r_delta  = None
    m5.metric("Risk per trade", _r_label, delta=_r_delta)

    # ── P-tier breakdown (bot mode only) ───────────────────────────────────────
    if bot_mode and sum(ptier_counts.values()) > 0:
        _total_pt = sum(ptier_counts.values())
        _p3_pct = ptier_counts["P3"] / _total_pt * 100
        _p1_pct = ptier_counts["P1"] / _total_pt * 100
        _p2_pct = ptier_counts["P2"] / _total_pt * 100
        st.markdown(
            f"<div style='font-size:12px;color:#546e7a;margin-bottom:6px;'>"
            f"P-tier breakdown: "
            f"<span style='color:#80cbc4;font-weight:600;'>P3 {ptier_counts['P3']:,} ({_p3_pct:.0f}%)</span> morning TCS≥70 ×1.50 &nbsp;·&nbsp; "
            f"<span style='color:#9fa8da;font-weight:600;'>P1 {ptier_counts['P1']:,} ({_p1_pct:.0f}%)</span> intraday TCS≥70 ×1.25 &nbsp;·&nbsp; "
            f"<span style='color:#546e7a;font-weight:600;'>P2 {ptier_counts['P2']:,} ({_p2_pct:.0f}%)</span> baseline ×1.00"
            f"</div>",
            unsafe_allow_html=True,
        )

    # ── Equity curve chart ─────────────────────────────────────────────────────
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=list(range(len(curve))),
        y=curve,
        mode="lines",
        line=dict(color="#80cbc4", width=2),
        name="Equity",
        fill="tozeroy",
        fillcolor="rgba(128,203,196,0.08)",
    ))
    fig_eq.add_hline(
        y=float(pnl_equity),
        line_dash="dot", line_color="#546e7a",
        annotation_text=f"Start ${pnl_equity:,}",
        annotation_font_color="#546e7a",
    )
    fig_eq.update_layout(
        plot_bgcolor="#0e0e1e", paper_bgcolor="#0e0e1e",
        font=dict(color="#9fa8da"),
        xaxis=dict(title="Trade #", gridcolor="#1e1e3a"),
        yaxis=dict(title="Account ($)", gridcolor="#1e1e3a",
                   tickprefix="$", tickformat=",.0f"),
        margin=dict(t=20, b=30, l=10, r=10),
        height=320, showlegend=False,
    )
    st.plotly_chart(fig_eq, use_container_width=True)

    # ── Drawdown chart ─────────────────────────────────────────────────────────
    with st.expander("Show Drawdown Chart"):
        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=list(range(len(drawdown_pcts))),
            y=[-d for d in drawdown_pcts],
            mode="lines",
            fill="tozeroy",
            line=dict(color="#ef9a9a", width=1.5),
            fillcolor="rgba(239,154,154,0.12)",
            name="Drawdown %",
        ))
        fig_dd.update_layout(
            plot_bgcolor="#0e0e1e", paper_bgcolor="#0e0e1e",
            font=dict(color="#9fa8da"),
            xaxis=dict(title="Trade #", gridcolor="#1e1e3a"),
            yaxis=dict(title="Drawdown (% from peak)", gridcolor="#1e1e3a",
                       ticksuffix="%"),
            margin=dict(t=10, b=30, l=10, r=10),
            height=220, showlegend=False,
        )
        st.plotly_chart(fig_dd, use_container_width=True)

    if bot_mode:
        sizing_label = "2.1% of equity / trade · $4,000 hard cap · P-tier mult (live bot logic)"
    elif pnl_fixed_risk is not None:
        _cmp_sfx = " (compounding)" if pnl_pos_compound else ""
        sizing_label = f"${pnl_fixed_risk:,}/trade fixed risk{_cmp_sfx}"
    else:
        sizing_label = f"{pnl_risk_pct}% of equity (compounding)"
    st.caption(
        f"Simulation: {_sim_n:,} trades (capped {pnl_max_per_day}/day by TCS rank, {s3['n']:,} total signals) · {sizing_label} · "
        "sorted chronologically by sim_date. "
        "Does not include slippage, commissions, or execution gaps. "
        "Past backtest performance does not guarantee future results."
    )

st.divider()
st.caption(f"Dataset: {len(all_rows):,} total breakout trades (actual_outcome = Bullish/Bearish Break, pnl_r_sim not null). Cache refreshes every 60 min.")
