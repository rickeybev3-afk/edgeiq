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
import sys, os
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
        "pnl_r_sim,scan_type,sim_date"
    )
    all_rows = []
    offset   = 0
    while True:
        page = PAGE_SIZE if max_rows == 0 else min(PAGE_SIZE, max_rows - len(all_rows))
        if page <= 0:
            break
        resp = (
            SUPABASE.table("backtest_sim_runs")
            .select(cols)
            .eq("user_id", user_id)
            .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
            .not_.is_("pnl_r_sim", "null")
            .range(offset, offset + page - 1)
            .execute()
        )
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
all_rows = load_breakout_rows(USER_ID, MAX_ROWS)
if not all_rows:
    st.error("No historical breakout data found. Run a batch backtest first.")
    st.stop()

# ── Sidebar / controls ────────────────────────────────────────────────────────
st.markdown("### Controls")
ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([2, 2, 1.5, 1.5])

with ctrl_col1:
    tcs_min = st.slider(
        "Min TCS score", min_value=30, max_value=85, value=50, step=5,
        help="Trade Conviction Score — composite signal strength. 50 = solid floor, 70+ = high-conviction. Higher = fewer but cleaner trades."
    )

with ctrl_col2:
    ib_max = st.slider(
        "Max IB range %", min_value=3.0, max_value=25.0, value=10.0, step=0.5,
        help="Initial Balance range as % of open price. Wide IBs = chaotic structure. 10% is the standard ceiling; lower = tighter, calmer setups."
    )

with ctrl_col3:
    use_vwap = st.toggle("Require VWAP side", value=True,
                         help="Only take Bullish Breaks where close was above VWAP (and Bearish Breaks below). Removes setups fighting the intraday trend.")

with ctrl_col4:
    scan_type_filter = st.selectbox("Scan type", ["All", "morning", "intraday"],
                                    help="Filter to only morning (pre-10:30) or intraday scans, or show all.")

st.divider()

# ── Filter rows ───────────────────────────────────────────────────────────────
# Apply scan type filter globally if selected
src = all_rows if scan_type_filter == "All" else [
    r for r in all_rows if r.get("scan_type") == scan_type_filter
]

# Layer 0: no filters
s0 = compute_stats(src)

# Layer 1: TCS
tcs_pass = [r for r in src if (r.get("tcs") or 0) >= tcs_min]
s1 = compute_stats(tcs_pass)

# Layer 2: + IB range
has_ib    = [r for r in tcs_pass if r.get("ib_range_pct") is not None]
ib_pass   = [r for r in has_ib   if r["ib_range_pct"] < ib_max]
ib_wide   = [r for r in has_ib   if r["ib_range_pct"] >= ib_max]
# Trades that had no ib_range_pct — pass through
no_ib     = [r for r in tcs_pass if r.get("ib_range_pct") is None]
after_ib  = ib_pass + no_ib
s2 = compute_stats(after_ib)

# Layer 3: + VWAP
if use_vwap:
    final       = [r for r in after_ib if vwap_aligned(r)]
    vwap_rej    = [r for r in after_ib if not vwap_aligned(r)]
else:
    final       = after_ib
    vwap_rej    = []
s3 = compute_stats(final)

# ── Filter funnel cards ───────────────────────────────────────────────────────
st.markdown("### Filter Funnel")
fc0, fca, fc1, fcb, fc2, fcc, fc3 = st.columns([3, 0.4, 3, 0.4, 3, 0.4, 3])

with fc0:
    stat_card("All Breakout Trades", s0)
with fca:
    st.markdown('<div class="funnel-arrow"><span>▶</span></div>', unsafe_allow_html=True)
with fc1:
    stat_card(f"TCS ≥ {tcs_min}", s1, active=True)
with fcb:
    st.markdown('<div class="funnel-arrow"><span>▶</span></div>', unsafe_allow_html=True)
with fc2:
    stat_card(f"+ IB < {ib_max:.1f}%", s2, active=True)
with fcc:
    st.markdown('<div class="funnel-arrow"><span>▶</span></div>', unsafe_allow_html=True)
with fc3:
    stat_card(f"{'+ VWAP aligned' if use_vwap else '(VWAP off)'} — Live Settings", s3, active=True)

# ── Rejection summary ─────────────────────────────────────────────────────────
rj1 = s0["n"] - s1["n"]
rj2 = s1["n"] - s2["n"]
rj3 = s2["n"] - s3["n"]

st.markdown("&nbsp;")
rj_col1, rj_col2, rj_col3 = st.columns(3)
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
with rj_col3:
    if rj3 > 0 and use_vwap:
        vwap_rej_stats = compute_stats(vwap_rej)
        st.markdown(
            f'<div class="reject-badge">✂ VWAP filter removes <b>{rj3:,}</b> trades '
            f'({rj3/s2["n"]*100:.0f}%) — those had <b>{vwap_rej_stats["wr"]:.0f}%</b> WR</div>',
            unsafe_allow_html=True
        )

st.divider()

# ── Charts row ────────────────────────────────────────────────────────────────
chart_left, chart_right = st.columns(2)

# Left: WR waterfall ──────────────────────────────────────────────────────────
with chart_left:
    st.markdown("#### Win-Rate by Filter Layer")
    layers_labels = [
        "Unfiltered",
        f"TCS >= {tcs_min}",
        f"+ IB < {ib_max:.1f}%",
        f"{'+ VWAP' if use_vwap else '(no VWAP)'} [Live]",
    ]
    layers_wr  = [s0["wr"], s1["wr"], s2["wr"], s3["wr"]]
    layers_exp = [s0["exp"], s1["exp"], s2["exp"], s3["exp"]]
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
    f"Simulates your account running through every trade in the **{s3['n']:,}-trade "
    "live-filter set**, in chronological order. Adjust sizing below."
)

pnl_c1, pnl_c2, pnl_c3 = st.columns(3)
with pnl_c1:
    pnl_equity = st.number_input(
        "Starting equity ($)", min_value=1000, max_value=500_000,
        value=7000, step=500, key="fs_pnl_equity",
    )
with pnl_c2:
    pnl_risk_mode = st.radio(
        "Risk sizing",
        ["Fixed position size ($)", "Fixed risk $ per trade", "% of equity (compounding)"],
        horizontal=False, key="fs_pnl_mode",
    )
with pnl_c3:
    if pnl_risk_mode == "Fixed position size ($)":
        pnl_pos_size = st.number_input(
            "Position size ($)", min_value=100, max_value=500_000,
            value=1500, step=100, key="fs_pnl_pos_size",
            help="Total dollars put into each trade (e.g. $1,500).",
        )
        pnl_stop_pct = st.number_input(
            "Avg stop % (of position)", min_value=1.0, max_value=50.0,
            value=10.0, step=0.5, key="fs_pnl_stop_pct",
            help="Your typical stop-loss as a % of position size. 10% stop on $1,500 = $150 risk (1R).",
        )
        pnl_fixed_risk = round(float(pnl_pos_size) * float(pnl_stop_pct) / 100.0, 2)
        pnl_risk_pct = None
        pnl_pos_compound = st.checkbox(
            "Compound position size", value=False, key="fs_pnl_pos_compound",
            help="Scale position size as account grows (same cap as % mode).",
        )
        st.caption(f"→ 1R = **${pnl_fixed_risk:,.0f}** per trade")
    elif pnl_risk_mode == "Fixed risk $ per trade":
        pnl_fixed_risk = st.number_input(
            "Risk $ per trade (1R)", min_value=10, max_value=50_000,
            value=150, step=10, key="fs_pnl_risk",
            help="Dollar amount you lose when stopped out (1R). E.g. $150 = 2.1% of a $7k account.",
        )
        pnl_risk_pct = None
        pnl_pos_compound = False
    else:
        pnl_risk_pct = st.number_input(
            "Risk % per trade", min_value=0.5, max_value=10.0,
            value=2.1, step=0.1, key="fs_pnl_risk_pct",
            help="% of current equity risked per trade (1R = this amount). Compounds as equity grows.",
        )
        pnl_fixed_risk = None
        pnl_pos_compound = False

# ── Date range filter ──────────────────────────────────────────────────────────
_all_sorted = sorted(final, key=lambda r: r.get("sim_date") or "")
_all_sim_dates = [r["sim_date"] for r in _all_sorted if r.get("sim_date")]
_data_min = datetime.strptime(_all_sim_dates[0],  "%Y-%m-%d").date() if _all_sim_dates else datetime(2021, 6, 1).date()
_data_max = datetime.strptime(_all_sim_dates[-1], "%Y-%m-%d").date() if _all_sim_dates else datetime.today().date()

_dr_c1, _dr_c2 = st.columns(2)
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

if s3["n"] == 0:
    st.info("No trades pass the current filters. Adjust sliders above.")
else:
    # ── Sort and extract date range ────────────────────────────────────────────
    sorted_trades = [
        r for r in _all_sorted
        if r.get("sim_date")
        and str(pnl_date_start) <= r["sim_date"] <= str(pnl_date_end)
    ]
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
    COMPOUND_CAP = 20.0  # same cap used in the main backtest engine
    eq   = _start_eq
    curve = [eq]
    peak  = eq
    max_dd_pct    = 0.0
    max_dd_dollar = 0.0
    drawdown_pcts = [0.0]
    cap_hit = False

    for trade in sorted_trades:
        r_val = trade.get("pnl_r_sim") or 0.0
        if pnl_fixed_risk is not None:
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
    avg_trade_amt  = (final_equity - float(pnl_equity)) / s3["n"] if s3["n"] else 0

    # Annualised return (CAGR) — skip if period too short to annualise meaningfully
    ann_return = None
    if trading_years and trading_years >= 0.25 and float(pnl_equity) > 0 and final_equity > 0:
        try:
            _raw_cagr = ((final_equity / float(pnl_equity)) ** (1.0 / trading_years) - 1) * 100
            ann_return = _raw_cagr if _raw_cagr <= 99_999 else None  # cap at 99,999%
        except (OverflowError, ZeroDivisionError, ValueError):
            ann_return = None

    trades_per_yr = s3["n"] / trading_years if trading_years else None

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
    if cap_hit:
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
    if pnl_fixed_risk is not None:
        _compound_note = " (compounding)" if pnl_pos_compound else ""
        _r_label = f"1R = {fmt_money(pnl_fixed_risk)}{_compound_note}"
    elif cap_hit:
        _r_label = f"1R = {pnl_risk_pct}% of equity (capped {int(COMPOUND_CAP)}×)"
    else:
        _r_label = f"1R = {pnl_risk_pct}% of equity"
    m5.metric("Risk per trade",  _r_label, delta=None)

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

    sizing_label = (
        f"${pnl_fixed_risk:,}/trade fixed risk"
        if pnl_risk_mode == "Fixed $ per trade"
        else f"{pnl_risk_pct}% of equity (compounding)"
    )
    st.caption(
        f"Simulation: {s3['n']:,} batch-backtest trades · {sizing_label} · "
        "sorted chronologically by sim_date. "
        "Does not include slippage, commissions, or execution gaps. "
        "Past backtest performance does not guarantee future results."
    )

st.divider()
st.caption(f"Dataset: {len(all_rows):,} total breakout trades (actual_outcome = Bullish/Bearish Break, pnl_r_sim not null). Cache refreshes every 60 min.")
