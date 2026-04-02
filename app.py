import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta, time as dtime
import time
import pytz
import os

from backend import *
from backend import (
    _compute_value_area, _strip_emoji,
    _parse_batch_pairs, _RECALIBRATE_EVERY, _BRAIN_WEIGHT_KEYS,
    _JOURNAL_COLS, _find_peaks, _is_strong_hvn, _detect_double_distribution,
    _label_to_weight_key, _save_brain_weights, _stream_worker, _GRADE_COLORS, _GRADE_SCORE,
)

st.set_page_config(page_title="Volume Profile Dashboard", page_icon="📊", layout="wide")

# ── Session state ──────────────────────────────────────────────────────────────
_DEFAULTS = {
    "live_active": False,
    "live_bars": [],
    "live_current_bar": None,
    "live_trades": deque(maxlen=3000),
    "live_thread": None,
    "live_stop_event": None,
    "live_queue": None,
    "live_ticker": "",
    "live_error": None,
    # Alert state
    "tcs_fired_high": False,   # True once ≥ 80% crossed this session
    "tcs_was_high": False,     # True while TCS was ≥ 60% (for chop-drop detection)
    "sound_trigger": 0,        # Incremented to force fresh audio iframe
    # RVOL / Sector cache — pre-fetched once per analysis session
    "rvol_avg_vol": None,          # Average daily volume (5-day lookback)
    "sector_pct_chg": 0.0,         # Sector ETF % change for current date
    "rvol_intraday_curve": None,   # Per-minute cumulative vol curve (390 elements)
    # Ticker widget state (explicit key so scanner can override)
    "ticker_input": "AAPL",
    # Auto-run flag: scanner sets this to True to trigger historical analysis on next rerun
    "auto_run": False,
    # Gap Scanner results cache
    "scanner_results": [],         # [{ticker, price, gap_pct, pm_vol, avg_pm_vol, pm_rvol}]
    "scanner_last_run": None,      # datetime of last successful scan
    # Last analysis snapshot — used by Live Pulse header + Log Entry
    "last_analysis_state": None,
    # Trade journal active tab state
    "active_tab": 0,
    # Position tracking
    "position_in":          False,
    "position_avg_entry":   0.0,
    "position_peak_price":  0.0,
    "position_ticker":      "",
    "position_shares":      0,
    "position_structure":   "",
    # MarketBrain — stores the live predicted structure between reruns
    "brain_session_correct":  0,    # correct predictions this session
    "brain_session_total":    0,    # total comparisons this session
    "brain_last_compared":    "",   # "TICKER_YYYY-MM-DD" — dedup key
    "brain_predicted":        None,
    "brain_ib_high":        0.0,
    "brain_ib_low":         float("inf"),
    "brain_ib_set":         False,
    "brain_high_touched":   False,
    "brain_low_touched":    False,
    # Replay mode
    "replay_bars":          None,   # full-day DataFrame (all bars)
    "replay_bar_idx":       0,      # index of current visible bar (0-based)
    "replay_playing":       False,  # auto-advance in progress
    "replay_speed":         1,      # bars advanced per step
    "replay_ticker":        "",
    "replay_date":          None,
    "replay_avg_vol":       None,
    "replay_intraday_curve": None,
    "replay_sector_bonus":  0.0,
    # Small Account Challenge tab
    "sa_account_bal":       5000.0,
    "sa_risk_pct":          2.0,
    "sa_pdt_used":          0,
    "sa_news_confirmed":    False,
    "sa_float_est":         0.0,
    "last_bars":            None,   # Real bars from last analysis run
    "sa_bin_centers":       None,   # VP bin centers for SA tab
    "sa_vap":               None,   # VP volumes for SA tab
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Restore today's brain accuracy counters from Supabase on first load ────────
if st.session_state.brain_session_total == 0:
    try:
        _restore_df = load_accuracy_tracker()
        if "timestamp" in _restore_df.columns and not _restore_df.empty:
            _today_str  = datetime.now(EASTERN).strftime("%Y-%m-%d")
            _today_rows = _restore_df[
                _restore_df["timestamp"].astype(str).str.startswith(_today_str)
            ]
            if not _today_rows.empty and "correct" in _today_rows.columns:
                st.session_state.brain_session_total   = int(len(_today_rows))
                st.session_state.brain_session_correct = int(
                    (_today_rows["correct"] == "✅").sum())
                if "compare_key" in _today_rows.columns:
                    _non_empty = _today_rows["compare_key"].dropna()
                    _non_empty = _non_empty[_non_empty.astype(str).str.strip() != ""]
                    if not _non_empty.empty:
                        st.session_state.brain_last_compared = str(_non_empty.iloc[-1])
    except Exception:
        pass  # safe fallback — counters stay at 0

# ── Audio JS (Web Audio API, synthesised tones — no external files) ────────────
_CHIME_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[523.25,0],[659.25,0.18],[783.99,0.36],[1046.50,0.54]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='sine'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.26,t+0.04);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.42);
      o.start(t); o.stop(t+0.43);
    });
  }catch(e){}
})();"""

_LOW_TONE_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[240,0],[200,0.26],[155,0.52]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='triangle'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.32,t+0.05);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.40);
      o.start(t); o.stop(t+0.41);
    });
  }catch(e){}
})();"""

_TARGET_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[1174.66,0],[1318.51,0.14],[1568.0,0.28],[1318.51,0.42]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='triangle'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.30,t+0.03);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.55);
      o.start(t); o.stop(t+0.56);
    });
  }catch(e){}
})();"""

EASTERN = pytz.timezone("America/New_York")

# ══════════════════════════════════════════════════════════════════════════════
# CORE MATH
# ══════════════════════════════════════════════════════════════════════════════

def check_tcs_alerts(tcs: float, audio_enabled: bool):
    """Fire visual toast + audio when TCS crosses key thresholds."""
    import streamlit.components.v1 as components

    # ── HIGH CONVICTION: TCS ≥ 80%, fires only once per session ───────────────
    if tcs >= 80 and not st.session_state.tcs_fired_high:
        st.session_state.tcs_fired_high = True
        st.session_state.tcs_was_high = True
        st.toast("🚀 HIGH CONVICTION TREND DETECTED", icon="🚀")
        if audio_enabled:
            n = st.session_state.sound_trigger + 1
            st.session_state.sound_trigger = n
            components.html(
                f'<script>/* hc:{n} */{_CHIME_JS}</script>',
                height=0, scrolling=False
            )

    # Track whether TCS has been "high" (≥ 60%) so we can detect a drop
    elif tcs >= 60:
        st.session_state.tcs_was_high = True

    # ── CHOP RISK: TCS drops below 30 after being high ────────────────────────
    if tcs < 30 and st.session_state.tcs_was_high:
        st.session_state.tcs_was_high = False
        st.toast("⚠️ CHOP RISK INCREASED", icon="⚠️")
        if audio_enabled:
            n = st.session_state.sound_trigger + 1
            st.session_state.sound_trigger = n
            components.html(
                f'<script>/* cr:{n} */{_LOW_TONE_JS}</script>',
                height=0, scrolling=False
            )


# ══════════════════════════════════════════════════════════════════════════════
# TARGET ZONES
# ══════════════════════════════════════════════════════════════════════════════

def check_target_alerts(price, targets, audio_enabled):
    """Fire a unique 'Target Reached' sound when price touches a target zone (0.5% tol)."""
    import streamlit.components.v1 as components
    if not audio_enabled or not targets or price is None:
        return
    tol = price * 0.005
    for tz in targets:
        key = f"target_fired_{tz['type']}_{tz['price']:.2f}"
        if abs(price - tz["price"]) <= tol and not st.session_state.get(key, False):
            st.session_state[key] = True
            st.toast(f"🎯 Target Reached — {tz['label']} at ${tz['price']:.2f}", icon="🎯")
            n = st.session_state.get("sound_trigger", 0) + 1
            st.session_state["sound_trigger"] = n
            components.html(
                f'<script>/* tr:{n} */{_TARGET_JS}</script>',
                height=0, scrolling=False
            )


# ══════════════════════════════════════════════════════════════════════════════
# LIVE STREAM
# ══════════════════════════════════════════════════════════════════════════════

def render_log_entry_ui():
    """Show the Notes box + LOG ENTRY button below the chart."""
    state = st.session_state.get("last_analysis_state")
    if not state:
        return
    with st.expander("💾 Log This Trade Entry", expanded=True):
        ticker  = state.get("ticker", "?")
        price   = state.get("price", 0.0)
        tcs     = state.get("tcs", 0.0)
        rvol    = state.get("rvol") or 0.0
        struct  = state.get("structure", "?")
        st.markdown(
            f"**Ready to log:** `{ticker}` @ `${price:.2f}` — "
            f"TCS `{tcs:.0f}` · RVOL `{rvol:.2f}x` · Structure `{struct}`"
        )
        notes = st.text_input(
            "Mental State / Notes",
            placeholder="e.g. Calm, FOMO, Greed, Hesitated...",
            key="journal_notes_input",
        )
        if st.button("💾 LOG ENTRY", use_container_width=True, key="journal_log_btn"):
            grade, reason = compute_trade_grade(
                state.get("rvol"), state.get("tcs"), state.get("price"),
                state.get("ib_high"), state.get("ib_low"), state.get("structure"),
            )
            entry = {
                "timestamp": datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
                "ticker":    state.get("ticker", ""),
                "price":     round(state.get("price", 0.0), 4),
                "structure": state.get("structure", ""),
                "tcs":       round(state.get("tcs", 0.0), 1),
                "rvol":      round(state.get("rvol") or 0.0, 2),
                "ib_high":   round(state.get("ib_high") or 0.0, 4),
                "ib_low":    round(state.get("ib_low") or 0.0, 4),
                "notes":     notes,
                "grade":     grade,
                "grade_reason": reason,
            }
            save_journal_entry(entry)
            gc = _GRADE_COLORS.get(grade, "#aaa")
            st.success(f"Logged! **Grade {grade}** — {reason}")
            st.markdown(
                f'<div style="display:inline-block; background:{gc}22; border:2px solid {gc}; '
                f'border-radius:50%; width:52px; height:52px; line-height:52px; '
                f'text-align:center; font-size:24px; font-weight:900; color:{gc};">'
                f'{grade}</div>',
                unsafe_allow_html=True,
            )


def render_journal_tab():
    """Render the 📖 My Journal tab."""
    df = load_journal()

    cola, colb = st.columns([1, 1])
    with cola:
        st.subheader("📖 My Trade Journal")
    with colb:
        if not df.empty:
            csv_bytes = df.to_csv(index=False).encode()
            st.download_button(
                "⬇️ Download Journal (CSV)",
                data=csv_bytes,
                file_name=f"trade_journal_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    if df.empty:
        st.info("No entries yet. Run an analysis and click **💾 LOG ENTRY** under the chart.")
        return

    # Grade badges + table
    st.markdown("---")
    for _, row in df.iterrows():
        grade = str(row.get("grade", "?"))
        gc = _GRADE_COLORS.get(grade, "#aaaaaa")
        reason = row.get("grade_reason", "")
        ts = row.get("timestamp", "")
        sym = row.get("ticker", "")
        price = row.get("price", "")
        struct = row.get("structure", "")
        tcs_v = row.get("tcs", "")
        rvol_v = row.get("rvol", "")
        notes_v = row.get("notes", "")

        st.markdown(f"""
        <div style="display:flex; gap:16px; align-items:center; background:#12122288;
                    border:1px solid #2a2a4a; border-radius:10px;
                    padding:12px 18px; margin:8px 0;">
            <div style="flex-shrink:0; width:52px; height:52px; border-radius:50%;
                        background:{gc}22; border:2.5px solid {gc};
                        display:flex; align-items:center; justify-content:center;
                        font-size:24px; font-weight:900; color:{gc};">{grade}</div>
            <div style="flex:1; min-width:0;">
                <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:baseline;">
                    <span style="font-size:20px; font-weight:800; color:#e0e0e0;">{sym}</span>
                    <span style="font-size:13px; color:#aaa;">${price}</span>
                    <span style="font-size:11px; color:#666;">{ts}</span>
                </div>
                <div style="font-size:12px; color:#90caf9; margin:2px 0;">{struct}</div>
                <div style="font-size:11px; color:#888;">
                    TCS {tcs_v}%  ·  RVOL {rvol_v}×
                    {f'  ·  <em>{notes_v}</em>' if notes_v else ''}
                </div>
                <div style="font-size:12px; color:{gc}; margin-top:4px;">{reason}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # Equity curve — grade average over entries
    st.markdown("---")
    st.markdown("**Grade Discipline Curve**")
    df2 = df.copy()
    df2["grade_score"] = df2["grade"].map(_GRADE_SCORE).fillna(1)
    df2["entry_num"]   = range(1, len(df2) + 1)
    df2["rolling_avg"] = df2["grade_score"].expanding().mean()

    import plotly.graph_objects as _go
    fig = _go.Figure()
    fig.add_trace(_go.Scatter(
        x=df2["entry_num"], y=df2["rolling_avg"],
        mode="lines+markers",
        line=dict(color="#00bcd4", width=2.5),
        marker=dict(size=7, color=df2["grade_score"].map(
            {4: "#4caf50", 3: "#26a69a", 2: "#ffa726", 1: "#ef5350"}
        ).fillna("#aaa")),
        name="Grade Average",
        hovertemplate="Entry %{x} — Avg %{y:.2f}<extra></extra>",
    ))
    fig.add_hline(y=3.0, line=dict(color="rgba(76,175,80,0.4)", dash="dot"),
                  annotation_text="B threshold", annotation_font_color="#4caf50")
    fig.update_layout(
        paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"), height=220,
        xaxis=dict(title="Entry #", gridcolor="#2a2a4a"),
        yaxis=dict(title="Avg Grade (F=1 \u2013 A=4)", gridcolor="#2a2a4a",
                   tickvals=[1, 2, 3, 4], ticktext=["F", "C", "B", "A"],
                   range=[0.5, 4.5]),
        margin=dict(l=10, r=10, t=20, b=40),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# CHART & RENDER
# ══════════════════════════════════════════════════════════════════════════════

def build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, title,
                target_zones=None, position=None):
    # ── Reindex to a full 9:30–16:00 minute grid so the chart ALWAYS
    #    anchors at 9:30 ET, even for thinly-traded small-caps whose first
    #    real trade doesn't occur until 9:31 or later.
    def _et_label(ts):
        try:
            return pd.Timestamp(ts).tz_convert(EASTERN).strftime("%H:%M ET")
        except Exception:
            return str(ts)[-8:]

    # Keep a handle to the original (real) bars before we expand the index
    _real_df = df.copy()
    _current_price = float(_real_df["close"].dropna().iloc[-1]) if not _real_df.empty else None

    try:
        _first = df.index[0]
        _last_real = df.index[-1]
        _d = _first.date()
        _open_et  = EASTERN.localize(datetime(_d.year, _d.month, _d.day,  9, 30))
        _close_et = EASTERN.localize(datetime(_d.year, _d.month, _d.day, 16,  0))
        _grid_end = min(_last_real, _close_et)
        _full_idx = pd.date_range(_open_et, _grid_end, freq="1min")
        df = df.reindex(_full_idx)
    except Exception:
        pass

    x_labels = [_et_label(ts) for ts in df.index]
    x0, x1   = x_labels[0], x_labels[-1]

    # ── Value Area (70 % of session volume) ──────────────────────────────────
    val_price, vah_price = _compute_value_area(bin_centers, vap)

    fig = make_subplots(rows=1, cols=2, column_widths=[0.75, 0.25],
                        shared_yaxes=True, horizontal_spacing=0.01)

    # ── Candlestick ───────────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=x_labels, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
        showlegend=False,
    ), row=1, col=1)

    # ── Value Area shaded band on price chart (very subtle) ───────────────────
    if val_price is not None and vah_price is not None:
        fig.add_hrect(
            y0=val_price, y1=vah_price,
            fillcolor="rgba(92, 107, 192, 0.06)",
            line_width=0,
            row=1, col=1,
        )

    # ── Key Level lines — all with right-edge price labels ────────────────────
    # POC — gold solid, thickest
    fig.add_trace(go.Scatter(
        x=[x0, x1], y=[poc_price, poc_price],
        mode="lines+text",
        name="POC",
        line=dict(color="rgba(255,215,0,1)", width=2.5),
        text=["", f"  POC  ${poc_price:.2f}"],
        textposition="middle right",
        textfont=dict(color="rgba(255,215,0,0.95)", size=11, family="monospace"),
        legendrank=10,
    ), row=1, col=1)

    # IB High — solid green
    if ib_high is not None and ib_low is not None:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[ib_high, ib_high],
            mode="lines+text",
            name="IB High",
            line=dict(color="#00e676", width=2.0),
            text=["", f"  IB Hi ${ib_high:.2f}"],
            textposition="middle right",
            textfont=dict(color="#00e676", size=11, family="monospace"),
            legendrank=20,
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[ib_low, ib_low],
            mode="lines+text",
            name="IB Low",
            line=dict(color="#ff5252", width=2.0),
            text=["", f"  IB Lo ${ib_low:.2f}"],
            textposition="middle right",
            textfont=dict(color="#ff5252", size=11, family="monospace"),
            legendrank=30,
        ), row=1, col=1)

    # VAH / VAL — thin blue dashed, clearly labelled
    if vah_price is not None:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[vah_price, vah_price],
            mode="lines+text",
            name="VAH",
            line=dict(color="#64b5f6", width=1.2, dash="dot"),
            text=["", f"  VAH  ${vah_price:.2f}"],
            textposition="middle right",
            textfont=dict(color="#64b5f6", size=10, family="monospace"),
            legendrank=40,
        ), row=1, col=1)
    if val_price is not None:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[val_price, val_price],
            mode="lines+text",
            name="VAL",
            line=dict(color="#64b5f6", width=1.2, dash="dot"),
            text=["", f"  VAL  ${val_price:.2f}"],
            textposition="middle right",
            textfont=dict(color="#64b5f6", size=10, family="monospace"),
            legendrank=50,
        ), row=1, col=1)

    # Current Price — white dotted
    if _current_price is not None:
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[_current_price, _current_price],
            mode="lines+text",
            name="Last Price",
            line=dict(color="rgba(220,220,220,0.8)", width=1.0, dash="dot"),
            text=["", f"  ▶ ${_current_price:.2f}"],
            textposition="middle right",
            textfont=dict(color="#e0e0e0", size=11, family="monospace"),
            legendrank=60,
        ), row=1, col=1)

    # ── Dynamic Target Zone overlay ───────────────────────────────────────────
    lvn_idx_to_highlight = None
    if target_zones:
        for tz in target_zones:
            tp = tz["price"]
            tc = tz["color"]
            tl = tz["label"]
            fig.add_trace(go.Scatter(
                x=[x0, x1], y=[tp, tp], mode="lines+text",
                name=tl,
                line=dict(color=tc, width=1.4, dash="dot"),
                text=["", f"  {tl}"],
                textposition="top right",
                textfont=dict(color=tc, size=10),
                showlegend=False,
            ), row=1, col=1)
            if tz["type"] == "trend_extension":
                band = tp * 0.005
                fig.add_shape(
                    type="rect", xref="paper", x0=0, x1=0.75,
                    y0=tp - band, y1=tp + band,
                    fillcolor=tc + "28",
                    line=dict(width=0),
                    row=1, col=1,
                )
            if tz["type"] == "gap_fill" and "lvn_idx" in tz:
                lvn_idx_to_highlight = tz["lvn_idx"]

    # ── Volume Profile — intensity-based gradient coloring ────────────────────
    bw = float(bin_centers[1] - bin_centers[0]) if len(bin_centers) > 1 else 0.01
    max_vap = float(np.max(vap)) if len(vap) > 0 and np.max(vap) > 0 else 1.0
    colors = []
    for i, (p, v) in enumerate(zip(bin_centers, vap)):
        norm = v / max_vap
        is_poc = abs(p - poc_price) < bw * 0.5
        is_lvn = (lvn_idx_to_highlight is not None and i == lvn_idx_to_highlight)
        in_va  = (val_price is not None and vah_price is not None
                  and val_price <= p <= vah_price)

        if is_poc:
            colors.append("rgba(255,215,0,1.0)")         # bright gold
        elif is_lvn:
            colors.append("rgba(255,235,59,0.88)")       # yellow LVN marker
        elif norm > 0.55:
            # High Volume Node — amber → deep orange
            g = int(165 - (norm - 0.55) / 0.45 * 90)
            a = min(1.0, 0.80 + norm * 0.20)
            colors.append(f"rgba(255,{max(75,g)},10,{a:.2f})")
        elif in_va:
            # Inside Value Area — blue-indigo, brighter with more volume
            r = int(80 + norm * 80)
            g = int(120 + norm * 50)
            b = int(210 - norm * 30)
            a = 0.60 + norm * 0.30
            colors.append(f"rgba({r},{g},{b},{a:.2f})")
        else:
            # Outside Value Area — muted slate, barely visible
            b_ch = int(115 + norm * 30)
            a = 0.28 + norm * 0.30
            colors.append(f"rgba(55,70,{b_ch},{a:.2f})")

    fig.add_trace(go.Bar(
        x=vap, y=bin_centers, orientation="h",
        name="Vol Profile", marker_color=colors, opacity=0.95,
        showlegend=False,
    ), row=1, col=2)

    # ── POC tick on the volume profile panel ──────────────────────────────────
    fig.add_trace(go.Scatter(
        x=[max_vap * 1.02], y=[poc_price],
        mode="text",
        text=["◀ POC"],
        textfont=dict(color="rgba(255,215,0,0.85)", size=10, family="monospace"),
        showlegend=False,
    ), row=1, col=2)

    # ── Position overlay ──────────────────────────────────────────────────────
    if position and position.get("in"):
        avg_entry  = position["avg_entry"]
        peak_price = position["peak_price"]
        price_now  = float(df["close"].iloc[-1])
        shares     = position.get("shares", 0)
        pnl_pct    = (price_now - avg_entry) / avg_entry * 100 if avg_entry > 0 else 0
        pnl_dol    = (price_now - avg_entry) * shares if shares > 0 else 0
        pnl_color  = "#4caf50" if pnl_pct >= 0 else "#ef5350"

        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[avg_entry, avg_entry], mode="lines+text",
            name=f"Entry ${avg_entry:.2f}",
            line=dict(color="#ffffff", width=2.0, dash="solid"),
            text=["", f"  📍 ENTRY ${avg_entry:.2f}"],
            textposition="top right",
            textfont=dict(color="#ffffff", size=12),
            showlegend=False,
        ), row=1, col=1)

        if peak_price > avg_entry:
            fig.add_trace(go.Scatter(
                x=[x0, x1], y=[peak_price, peak_price], mode="lines+text",
                name=f"MFE ${peak_price:.2f}",
                line=dict(color="#00bcd4", width=1.5, dash="dash"),
                text=["", f"  ⬆ MFE ${peak_price:.2f}"],
                textposition="top right",
                textfont=dict(color="#00bcd4", size=11),
                showlegend=False,
            ), row=1, col=1)

        pnl_txt = (f"{'▲' if pnl_pct>=0 else '▼'} {abs(pnl_pct):.1f}%"
                   f"  (${pnl_dol:+.0f})" if shares > 0 else
                   f"{'▲' if pnl_pct>=0 else '▼'} {abs(pnl_pct):.1f}%")
        fig.add_annotation(
            xref="paper", yref="y",
            x=0.74, y=avg_entry,
            text=f"<b>{pnl_txt}</b>",
            showarrow=False,
            font=dict(color=pnl_color, size=13),
            bgcolor=pnl_color + "22",
            bordercolor=pnl_color,
            borderwidth=1,
            borderpad=4,
        )

    # ── Chart layout ──────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(text=title, font=dict(size=16, color="#e0e0e0")),
        paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
        font=dict(color="#c0c0d8"),
        height=660,
        xaxis=dict(
            rangeslider=dict(visible=False),
            gridcolor="#1c2040",
            showgrid=True,
            type="category",
        ),
        yaxis=dict(
            gridcolor="#1c2040",
            showgrid=True,
            tickformat=".2f",
            tickfont=dict(family="monospace", size=11, color="#8888aa"),
        ),
        xaxis2=dict(
            gridcolor="#1c2040",
            showgrid=False,
            title="",
            showticklabels=False,
        ),
        # ── Clean legend box — key levels only ────────────────────────────────
        legend=dict(
            bgcolor="rgba(8, 12, 40, 0.88)",
            bordercolor="rgba(80, 90, 160, 0.6)",
            borderwidth=1,
            font=dict(color="#c8c8e0", size=11, family="monospace"),
            x=0.01, y=0.99,
            xanchor="left", yanchor="top",
            title=dict(
                text="KEY LEVELS",
                font=dict(size=9, color="#5c6bc0"),
            ),
            tracegroupgap=1,
            itemsizing="constant",
        ),
        margin=dict(l=10, r=130, t=55, b=40),
    )
    fig.update_xaxes(nticks=16, tickangle=-45, row=1, col=1)
    return fig


def render_structure_banner(label, color, detail, probs, tcs,
                            is_runner=False, sector_bonus=0.0, insight=None):
    top3 = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:3]
    prob_pills = "".join(
        f'<span style="display:inline-block; background:{color}33; border:1px solid {color}66; '
        f'border-radius:4px; padding:2px 8px; margin:2px 4px; font-size:13px; color:#eee;">'
        f'<b>{n}</b> {p}%</span>'
        for n, p in top3
    )

    # ── TCS gauge colour logic ─────────────────────────────────────────────────
    if is_runner:
        # Gold → Electric Blue gradient for MULTI-DAY RUNNER / STOCK IN PLAY
        gauge_fill = "linear-gradient(90deg,#FFD700,#00BFFF)"
        gauge_color = "#FFD700"
        badge = ('<span style="background:#FFD70033; border:1px solid #FFD700; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#FFD700; '
                 'text-transform:uppercase; letter-spacing:1px; '
                 'box-shadow:0 0 8px #FFD70066;">⚡ RUNNER MODE</span>')
    elif tcs >= 70:
        gauge_fill = f"linear-gradient(90deg,#4caf5099,#4caf50)"
        gauge_color = "#4caf50"
        badge = ('<span style="background:#4caf5033; border:1px solid #4caf50; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#4caf50; '
                 'text-transform:uppercase; letter-spacing:1px;">🔥 HIGH CONVICTION</span>')
    elif tcs <= 30:
        gauge_fill = f"linear-gradient(90deg,#ef535099,#ef5350)"
        gauge_color = "#ef5350"
        badge = ('<span style="background:#ef535033; border:1px solid #ef5350; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#ef5350; '
                 'text-transform:uppercase; letter-spacing:1px;">⚠ CHOP RISK</span>')
    else:
        gauge_fill = f"linear-gradient(90deg,#ffa72699,#ffa726)"
        gauge_color = "#ffa726"
        badge = ""

    # Sector tailwind badge
    sector_badge = ""
    if sector_bonus > 0:
        sector_badge = ('<span style="background:#26a69a33; border:1px solid #26a69a; '
                        'border-radius:4px; padding:2px 10px; font-size:12px; font-weight:700; '
                        'color:#26a69a; text-transform:uppercase; letter-spacing:1px; '
                        'margin-left:6px;">🌊 SECTOR TAILWIND +10</span>')

    tcs_label = f"{tcs:.0f}%"
    if sector_bonus > 0:
        tcs_label += f" (+{sector_bonus:.0f} sector)"

    tcs_bar = f"""
    <div style="margin-top:10px; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px; white-space:nowrap;">
            Trend Confidence
        </span>
        <div style="flex:1; min-width:120px; max-width:220px; background:#2a2a4a;
                    border-radius:6px; height:12px; overflow:hidden;">
            <div style="width:{min(tcs,100)}%; background:{gauge_fill};
                        height:100%; border-radius:6px; transition:width 0.4s;"></div>
        </div>
        <span style="font-size:18px; font-weight:800; color:{gauge_color}; min-width:44px;">{tcs_label}</span>
        {badge}{sector_badge}
    </div>
    """

    # Runner glow border
    glow = f"box-shadow:0 0 18px {gauge_color}55;" if is_runner else ""

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{color}22,{color}0a);
                border-left:5px solid {color}; border-radius:8px;
                padding:14px 22px; margin:10px 0 4px 0; {glow}">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
            <div>
                <div style="font-size:26px; font-weight:800; color:{color}; letter-spacing:0.5px;">{label}</div>
                <div style="font-size:13px; color:#cccccc; margin-top:4px;">{detail}</div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:11px; color:#888; text-transform:uppercase;
                            letter-spacing:1px; margin-bottom:4px;">Structure Probability</div>
                <div>{prob_pills}</div>
            </div>
        </div>
        {tcs_bar}
    </div>
    """, unsafe_allow_html=True)

    # Key Insights box — separate call so Streamlit's markdown parser doesn't escape inner HTML
    if insight:
        st.markdown(
            f'<div style="margin-top:6px; background:#1a2744; border-left:3px solid {color}99;'
            f' border-radius:5px; padding:9px 14px;">'
            f'<span style="font-size:10px; color:#888; text-transform:uppercase;'
            f' letter-spacing:1px; font-weight:600;">KEY INSIGHTS</span><br>'
            f'<span style="font-size:13px; color:#d0d8f0; line-height:1.55;">{insight}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


def render_rvol_widget(rvol_val, label_str, label_color, is_runner):
    """Show the RVOL reading with appropriate colour and optional glow for runner stocks."""
    if rvol_val is None:
        return
    display_label = label_str if label_str else f"RVOL {rvol_val:.1f}×  — Normal Activity"
    if label_str is None:
        label_color = "#aaaaaa"

    runner_style = ""
    if is_runner:
        runner_style = ("box-shadow:0 0 16px #FFD70077; "
                        "border-color:#FFD700 !important; "
                        "animation:rvol-pulse 1.8s ease-in-out infinite;")

    st.markdown(f"""
    <style>
    @keyframes rvol-pulse {{
        0%   {{ box-shadow: 0 0 8px #FFD70044; }}
        50%  {{ box-shadow: 0 0 22px #FFD700cc; }}
        100% {{ box-shadow: 0 0 8px #FFD70044; }}
    }}
    </style>
    <div style="display:inline-flex; align-items:center; background:{label_color}11;
                border:2px solid {label_color}77; border-radius:8px;
                padding:8px 20px; margin:4px 0 6px 0; gap:16px; {runner_style}">
        <span style="font-size:12px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">RVOL</span>
        <span style="font-size:24px; font-weight:900; color:{label_color};">{rvol_val:.1f}×</span>
        <span style="font-size:14px; font-weight:700; color:{label_color};">{display_label}</span>
    </div>
    """, unsafe_allow_html=True)


def render_buy_sell_widget(bsp, rvol_val=None):
    """Buy/Sell Volume Pressure oscillator — 0-100 gauge with momentum arrow.

    Uses the blended CLV+Tick formula (same as ThinkScript Blended split).
    Fires a HIGH CONVICTION alert when RVOL > 3 AND buy pressure is ramping.
    """
    if bsp is None:
        return
    buy_pct    = bsp["buy_pct"]
    trend_now  = bsp["trend_now"]
    trend_prev = bsp["trend_prev"]
    delta      = trend_now - trend_prev

    if buy_pct >= 65:
        bar_color, label = "#4caf50", "🔼 BUY DOMINANT"
    elif buy_pct >= 57:
        bar_color, label = "#8bc34a", "↑ Buy Leaning"
    elif buy_pct >= 43:
        bar_color, label = "#ffa726", "⇔ Balanced"
    elif buy_pct >= 35:
        bar_color, label = "#ef9a9a", "↓ Sell Leaning"
    else:
        bar_color, label = "#ef5350", "🔽 SELL DOMINANT"

    if delta > 3:
        momentum, mom_color = f"▲ Ramping +{delta:.0f}%", "#4caf50"
    elif delta < -3:
        momentum, mom_color = f"▼ Cooling {delta:.0f}%", "#ef5350"
    else:
        momentum, mom_color = "→ Flat", "#aaaaaa"

    # ── High-conviction signal: RVOL > 3 + buy pressure ramping ──────────────
    rvol_gt3     = rvol_val is not None and rvol_val >= 3.0
    buy_ramping  = delta > 3 and buy_pct >= 55
    sell_ramping = delta < -3 and buy_pct <= 45
    hc_alert     = ""
    if rvol_gt3 and buy_ramping:
        hc_alert = (
            f'<div style="background:#4caf5022; border:1px solid #4caf5088; '
            f'border-radius:6px; padding:6px 12px; margin-bottom:6px; '
            f'font-size:12px; font-weight:700; color:#4caf50; text-align:center;">'
            f'🚀 HIGH CONVICTION BUY — RVOL {rvol_val:.1f}× + Buy Ramping'
            f'</div>'
        )
    elif rvol_gt3 and sell_ramping:
        hc_alert = (
            f'<div style="background:#ef535022; border:1px solid #ef535088; '
            f'border-radius:6px; padding:6px 12px; margin-bottom:6px; '
            f'font-size:12px; font-weight:700; color:#ef5350; text-align:center;">'
            f'🔻 HIGH CONVICTION SELL — RVOL {rvol_val:.1f}× + Sell Ramping'
            f'</div>'
        )

    st.markdown(f"""
    <div style="background:#1a1a2e; border:1px solid {bar_color}55; border-radius:8px;
                padding:10px 16px; margin:4px 0 6px 0;">
      {hc_alert}
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">
          Buy / Sell Pressure &nbsp;<span style="color:#555;">(CLV+Tick Blended)</span>
        </span>
        <span style="font-size:12px; font-weight:700; color:{bar_color};">{label}</span>
        <span style="font-size:11px; color:{mom_color};">{momentum}</span>
      </div>
      <div style="background:#333; border-radius:4px; height:14px; width:100%;
                  position:relative; overflow:hidden;">
        <div style="position:absolute; left:0; top:0; height:100%; width:{buy_pct:.1f}%;
                    background:{bar_color}; border-radius:4px;"></div>
        <div style="position:absolute; left:50%; top:0; height:100%;
                    width:2px; background:#ffffff44;"></div>
      </div>
      <div style="display:flex; justify-content:space-between; margin-top:5px;">
        <span style="font-size:11px; color:#ef5350;">🔴 Sell {bsp["sell_pct"]:.0f}%</span>
        <span style="font-size:11px; color:{bar_color}; font-weight:700;">🟢 Buy {buy_pct:.0f}%</span>
      </div>
    </div>
    """, unsafe_allow_html=True)


def render_model_prediction(outcome, reasoning):
    """Show the volume-price divergence model prediction in a styled text box.

    'Market Closed' outcome → shows a blue sleep-mode info box instead of
    a directional warning so stale/historical analysis isn't misread as live signal.
    """
    # ── Sleep mode: market is closed, suppress directional warnings ───────────
    if outcome == "Market Closed":
        st.markdown("""
        <div style="background:#0d47a114; border:1px solid #1565c066;
                    border-radius:8px; padding:12px 20px; margin:8px 0 4px 0;">
            <div style="font-size:10px; color:#5c8ecc; text-transform:uppercase;
                        letter-spacing:1.2px; margin-bottom:6px;">
                🤖 Model Prediction — Volume-Price Divergence
            </div>
            <div style="font-size:20px; font-weight:800; color:#64b5f6; margin-bottom:4px;">
                💤 MARKET CLOSED — Analyzing Historical Data
            </div>
            <div style="font-size:13px; color:#90caf9; line-height:1.6;">
                Live pattern alerts are suppressed outside 9:30 AM – 4:00 PM EST.
                Use the volume profile structure and TCS score for session-level context.
            </div>
        </div>
        """, unsafe_allow_html=True)
        return

    # ── Live / historical session directional signal ───────────────────────────
    _colors = {"Fake-out": "#ef5350", "High Conviction": "#4caf50", "Consolidation": "#ffa726"}
    _icons  = {"Fake-out": "⚠️", "High Conviction": "🎯", "Consolidation": "📊"}
    c    = _colors.get(outcome, "#aaaaaa")
    icon = _icons.get(outcome, "📊")
    st.markdown(f"""
    <div style="background:{c}0e; border:1px solid {c}44; border-radius:8px;
                padding:12px 20px; margin:8px 0 4px 0;">
        <div style="font-size:10px; color:#666; text-transform:uppercase;
                    letter-spacing:1.2px; margin-bottom:6px;">
            🤖 Model Prediction — Volume-Price Divergence
        </div>
        <div style="font-size:20px; font-weight:800; color:{c}; margin-bottom:4px;">
            {icon}&nbsp;{outcome}
        </div>
        <div style="font-size:13px; color:#c0c0c0; line-height:1.6;">
            {reasoning}
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_velocity_widget(df):
    vol, chg, direction = compute_volume_velocity(df)
    if vol is None:
        return
    color = "#26a69a" if direction == "↑" else "#ef5350" if direction == "↓" else "#aaa"
    chg_str = f"{direction} {chg:.1f}%" if chg is not None else "—"

    intensity = ""
    if chg is not None:
        if chg > 50:
            intensity = " — 🔥 Surging"
        elif chg > 20:
            intensity = " — ⬆ Accelerating"
        elif chg < -50:
            intensity = " — 🧊 Drying up"
        elif chg < -20:
            intensity = " — ⬇ Fading"

    st.markdown(f"""
    <div style="display:inline-flex; align-items:center; background:#16213e;
                border:1px solid #2a2a4a; border-radius:6px; padding:8px 18px; margin:4px 0 8px 0; gap:14px;">
        <span style="font-size:12px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">⚡ Vol Velocity</span>
        <span style="font-size:19px; font-weight:700; color:#e0e0e0;">{vol:,.0f}/min</span>
        <span style="font-size:16px; font-weight:600; color:{color};">{chg_str}{intensity}</span>
    </div>
    """, unsafe_allow_html=True)


def render_analysis(df, num_bins, ticker, chart_title, is_ib_live=False,
                    avg_daily_vol=None, sector_bonus=0.0, sector_etf="IWM",
                    intraday_curve=None, is_live=False):
    ib_high, ib_low = compute_initial_balance(df)
    bin_centers, vap, poc_price = compute_volume_profile(df, num_bins)
    # ── Cache raw bars + VP data for Small Account Challenge tab ─────────────
    st.session_state.last_bars      = df.copy()
    st.session_state.sa_bin_centers = bin_centers
    st.session_state.sa_vap         = vap
    _sa_val, _sa_vah = _compute_value_area(bin_centers, vap)
    label, color, detail, insight = classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price, avg_daily_vol=avg_daily_vol
    )
    probs = compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price)
    tcs = compute_tcs(df, ib_high, ib_low, poc_price, sector_bonus=sector_bonus)
    target_zones = compute_target_zones(df, ib_high, ib_low, bin_centers, vap, tcs)

    # ── High Conviction logger ─────────────────────────────────────────────────
    try:
        _top_struct, _top_prob = max(probs.items(), key=lambda x: x[1])
        if _top_prob >= HICONS_THRESHOLD and ib_high is not None and ib_low is not None:
            log_high_conviction(ticker, selected_date, _top_struct, _top_prob,
                                ib_high=ib_high, ib_low=ib_low, poc_price=poc_price)
    except Exception:
        pass
    audio_enabled = st.session_state.get("audio_alerts_enabled", True)
    check_tcs_alerts(tcs, audio_enabled)

    # ── MarketBrain — real-time structure prediction ───────────────────────────
    brain = MarketBrain()
    brain.load_from_session()
    rvol_pre = compute_rvol(df, intraday_curve=intraday_curve, avg_daily_vol=avg_daily_vol)
    try:
        _brain_ivp, _ = compute_ib_volume_stats(df, ib_high, ib_low) if (
            ib_high is not None and ib_low is not None) else (None, None)
    except Exception:
        _brain_ivp = None
    _brain_has_dd = _detect_double_distribution(bin_centers, vap) is not None
    brain.update(df, rvol=rvol_pre, ib_vol_pct=_brain_ivp,
                 poc_price=poc_price, has_double_dist=_brain_has_dd)
    st.session_state.brain_predicted = brain.prediction

    # ── Time context ───────────────────────────────────────────────────────────
    # elapsed_bars = minutes of session captured so far (used for time-seg RVOL + Fuel Check)
    elapsed_bars = len(df)
    # market_open:
    #   • Live mode  → check actual clock (suppress fake-out if market is closed)
    #   • Historical → True — session data is self-contained, show all signals
    market_open = is_market_open() if is_live else True

    # ── RVOL + Pattern Label ───────────────────────────────────────────────────
    price_start = float(df["open"].iloc[0]) if len(df) else 0.0
    price_now   = float(df["close"].iloc[-1]) if len(df) else 0.0
    pct_chg_today = (price_now - price_start) / price_start * 100.0 if price_start > 0 else 0.0

    rvol_val = compute_rvol(df, intraday_curve=intraday_curve, avg_daily_vol=avg_daily_vol)
    rvol_lbl, rvol_color, is_runner, is_play = rvol_classify(
        rvol_val, pct_chg_today,
        elapsed_bars=elapsed_bars if is_live else None,   # open-window check only in live mode
        price_now=price_now
    )

    # ── Model prediction ───────────────────────────────────────────────────────
    pred_outcome, pred_reasoning = compute_model_prediction(
        df, rvol_val, tcs, sector_bonus, market_open=market_open
    )

    day_high = float(df["high"].max())
    day_low  = float(df["low"].min())
    ib_range = (ib_high - ib_low) if ib_high and ib_low else 0.0

    # IB status badge for live mode
    if is_ib_live and ib_high and ib_low:
        now_est = datetime.now(EASTERN)
        mins_left = max(0, int((datetime.combine(date.today(), dtime(10, 30)) -
                                now_est.replace(tzinfo=None)).total_seconds() // 60))
        st.markdown(
            f'<div style="display:inline-block; background:#ff980033; border:1px solid #ff9800; '
            f'border-radius:4px; padding:3px 10px; font-size:13px; color:#ff9800; margin-bottom:8px;">'
            f'📐 IB FORMING — {mins_left} min remaining until 10:30 EST</div>',
            unsafe_allow_html=True
        )

    # ── Top metrics row ───────────────────────────────────────────────────────
    rvol_display = f"{rvol_val:.1f}×" if rvol_val is not None else "—"
    sector_display = (f"{sector_etf} +{sector_bonus:.0f}pts" if sector_bonus > 0
                      else f"{sector_etf} —")
    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)
    col1.metric("Bars", len(df))
    col2.metric("IB High", f"${ib_high:.2f}" if ib_high else "—")
    col3.metric("IB Low",  f"${ib_low:.2f}"  if ib_low  else "—")
    col4.metric("IB Range", f"${ib_range:.2f}")
    col5.metric("Day Range", f"${day_high - day_low:.2f}")
    col6.metric("POC", f"${poc_price:.2f}")
    col7.metric("RVOL", rvol_display)
    col8.metric("Sector", sector_display)

    # ── IB Volume Stats widget ─────────────────────────────────────────────────
    ib_vol_pct_disp, ib_range_ratio_disp = compute_ib_volume_stats(df, ib_high, ib_low)
    _ivp_pct = ib_vol_pct_disp * 100
    _irr_pct = ib_range_ratio_disp * 100
    # Color coding: balanced (green) vs directional (orange/red)
    _ivp_color = ("#4caf50" if _ivp_pct >= 60 else "#ffa726" if _ivp_pct >= 35 else "#ef5350")
    _irr_color = ("#4caf50" if _irr_pct >= 50 else "#ffa726" if _irr_pct >= 25 else "#ef5350")
    _ivp_label = "Balanced" if _ivp_pct >= 60 else ("Neutral" if _ivp_pct >= 35 else "Directional")
    _irr_label = "Contained" if _irr_pct >= 50 else ("Moderate" if _irr_pct >= 25 else "Expanded")
    st.markdown(
        f'<div style="background:#0f3460; border:1px solid #1a3a6e; border-radius:6px; '
        f'padding:8px 16px; margin:4px 0 6px 0; display:flex; align-items:center; gap:20px; flex-wrap:wrap;">'
        f'<span style="font-size:11px; color:#5c6bc0; text-transform:uppercase; '
        f'letter-spacing:1px; white-space:nowrap;">📐 IB Structure</span>'
        f'<span style="font-size:12px; color:#aaa;">IB Vol%: '
        f'<b style="color:{_ivp_color};">{_ivp_pct:.0f}%</b> '
        f'<span style="color:{_ivp_color}; font-size:11px;">({_ivp_label})</span></span>'
        f'<span style="color:#2a2a4a;">|</span>'
        f'<span style="font-size:12px; color:#aaa;">IB/Day Range: '
        f'<b style="color:{_irr_color};">{_irr_pct:.0f}%</b> '
        f'<span style="color:{_irr_color}; font-size:11px;">({_irr_label})</span></span>'
        f'<span style="color:#2a2a4a;">|</span>'
        f'<span style="font-size:11px; color:#555; white-space:nowrap;">'
        f'IB ${ib_high:.2f} – ${ib_low:.2f} &nbsp;|&nbsp; POC ${poc_price:.2f}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    render_velocity_widget(df)
    render_rvol_widget(rvol_val, rvol_lbl, rvol_color, is_runner)
    render_buy_sell_widget(compute_buy_sell_pressure(df), rvol_val=rvol_val)
    render_structure_banner(label, color, detail, probs, tcs,
                            is_runner=is_runner, sector_bonus=sector_bonus,
                            insight=insight)
    render_model_prediction(pred_outcome, pred_reasoning)

    # ── MarketBrain: compare prediction vs actual + running counter ───────────
    bc = brain.color()
    _brain_correct_now = False
    _brain_newly_logged = False

    # Auto-compare once IB is complete and brain has a real prediction
    if brain.ib_set and brain.prediction != "Analyzing IB…" \
            and ib_high is not None and ib_low is not None \
            and ib_high != float("inf") and ib_low != float("inf"):
        _today_str   = datetime.now(EASTERN).strftime("%Y-%m-%d")
        _compare_key = f"{ticker}_{_today_str}_{float(ib_high):.4f}_{float(ib_low):.4f}"

        # Dedup: check both session state AND the CSV (survives reloads)
        _already_in_csv = False
        if st.session_state.brain_last_compared != _compare_key:
            try:
                _chk = pd.read_csv(TRACKER_FILE) if os.path.exists(TRACKER_FILE) else pd.DataFrame()
                if "compare_key" in _chk.columns:
                    _already_in_csv = (_chk["compare_key"] == _compare_key).any()
            except Exception:
                pass

        if st.session_state.brain_last_compared != _compare_key and not _already_in_csv:
            st.session_state.brain_last_compared = _compare_key
            # Fuzzy match: strip emojis/punctuation and compare core words
            _pred_clean   = _strip_emoji(brain.prediction)
            _actual_clean = _strip_emoji(label)
            _brain_correct_now = (
                _pred_clean in _actual_clean or _actual_clean in _pred_clean
                or any(w in _actual_clean for w in _pred_clean.split() if len(w) > 4)
            )
            st.session_state.brain_session_total   += 1
            if _brain_correct_now:
                st.session_state.brain_session_correct += 1
            # Log to CSV with the compare_key stored for reload dedup
            log_accuracy_entry(ticker, brain.prediction, label,
                               compare_key=_compare_key)
            _brain_newly_logged = True
        elif st.session_state.brain_last_compared != _compare_key and _already_in_csv:
            # Already in CSV from a previous session — just sync the session key
            st.session_state.brain_last_compared = _compare_key

    _b_corr  = st.session_state.brain_session_correct
    _b_total = st.session_state.brain_session_total
    _b_rate  = (_b_corr / _b_total * 100) if _b_total > 0 else 0
    _counter_str = (f"Today: {_b_corr}/{_b_total} ({_b_rate:.0f}%)"
                    if _b_total > 0 else "Today: —")
    _counter_col = "#4caf50" if _b_rate >= 60 else "#ffa726" if _b_rate >= 40 else "#ef5350"

    # ── All-time win rate — reads directly from CSV, never resets ─────────────
    _at_total, _at_correct, _at_rate = 0, 0, None
    try:
        if os.path.exists(TRACKER_FILE):
            _at_df = pd.read_csv(TRACKER_FILE)
            _at_total = int(len(_at_df))
            if "correct" in _at_df.columns and _at_total > 0:
                _at_correct = int((_at_df["correct"] == "✅").sum())
                _at_rate    = round(float(_at_correct) / float(_at_total) * 100.0, 1)
    except Exception:
        _at_total, _at_correct, _at_rate = 0, 0, None

    _at_is_valid = (_at_rate is not None and isinstance(_at_rate, (int, float))
                    and _at_rate == _at_rate)   # NaN check
    _at_col = ("#4caf50" if _at_is_valid and _at_rate >= 60
               else "#ffa726" if _at_is_valid and _at_rate >= 40
               else "#ef5350" if _at_is_valid else "#555")
    _at_str  = f"{_at_rate:.0f}%" if _at_is_valid else "—"
    _at_lbl  = f"All-time: <b style='font-size:18px; color:{_at_col};'>{_at_str}</b>"
    if _at_is_valid:
        _at_lbl += f" <span style='font-size:10px; color:#555;'>({_at_correct}/{_at_total})</span>"

    st.markdown(
        f'<div style="background:{bc}11; border-left:3px solid {bc}; border-radius:6px; '
        f'padding:10px 16px; margin:6px 0 4px 0; display:flex; align-items:center; gap:16px; flex-wrap:wrap;">'
        f'<span style="font-size:11px; color:#888; text-transform:uppercase; '
        f'letter-spacing:1px; white-space:nowrap;">🧠 Brain</span>'
        f'<span style="font-size:15px; font-weight:700; color:{bc};">{brain.prediction}</span>'
        f'<span style="font-size:11px; color:#555;">vs <span style="color:{color};">{label}</span></span>'
        f'<span style="font-size:11px; color:#444; margin-left:auto;">|</span>'
        f'<span style="font-size:12px; color:#aaa;">{_at_lbl}</span>'
        f'<span style="font-size:11px; font-weight:600; color:{_counter_col}; '
        f'background:{_counter_col}22; padding:2px 8px; border-radius:4px; '
        f'border:1px solid {_counter_col}44; white-space:nowrap;">{_counter_str}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Flash notification when brain is newly correct / newly wrong ──────────
    if _brain_newly_logged:
        if _brain_correct_now:
            st.markdown(
                f'<div style="background:#4caf5022; border:1px solid #4caf50; border-radius:6px; '
                f'padding:8px 16px; margin:4px 0; font-size:13px; font-weight:700; color:#4caf50;">'
                f'✅ Brain Correct! Predicted <em>{brain.prediction}</em> — matches <em>{label}</em>. '
                f'Running accuracy: {_b_corr}/{_b_total} ({_b_rate:.0f}%)</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="background:#ffa72622; border:1px solid #ffa726; border-radius:6px; '
                f'padding:8px 16px; margin:4px 0; font-size:13px; color:#ffa726;">'
                f'🔄 Brain predicted <em>{brain.prediction}</em> — actual was <em>{label}</em>. '
                f'Data logged for learning. Running: {_b_corr}/{_b_total} ({_b_rate:.0f}%)</div>',
                unsafe_allow_html=True,
            )

    # ── Persist snapshot for Live Pulse header + Log Entry ────────────────────
    st.session_state.last_analysis_state = {
        "ticker":    ticker,
        "price":     price_now,
        "structure": label,
        "tcs":       tcs,
        "rvol":      rvol_val,
        "ib_high":   ib_high,
        "ib_low":    ib_low,
        "poc_price": poc_price,
        "val_price": _sa_val,
        "vah_price": _sa_vah,
        "pct_change": pct_chg_today,
        "rvol_color": rvol_color,
        "is_runner": is_runner,
        "label_color": color,
        "vol_velocity_str": "",
        "brain_predicted": brain.prediction,
    }

    # ── Update peak price + auto-alerts for open position ────────────────────
    if st.session_state.position_in and st.session_state.position_ticker == ticker:
        avg_entry = st.session_state.position_avg_entry

        # Track MFE
        if price_now > st.session_state.position_peak_price:
            st.session_state.position_peak_price = price_now
            save_position_state()

        # 1. Breakeven alert — price reached +2% from entry
        if avg_entry > 0 and price_now >= avg_entry * 1.02:
            be_pct = (price_now - avg_entry) / avg_entry * 100
            st.markdown(
                f'<div style="background:#ff980022; border:1px solid #ff9800; border-radius:6px; '
                f'padding:10px 18px; margin:6px 0; font-size:14px; font-weight:700; color:#ff9800;">'
                f'⚡ MOVE STOP TO BREAKEVEN — Price is +{be_pct:.1f}% from entry '
                f'(${avg_entry:.2f} → ${price_now:.2f}). '
                f'Set stop at ${avg_entry:.2f} to lock in a risk-free trade.</div>',
                unsafe_allow_html=True,
            )

        # 2. Auto Take-Profit — price hit IB High on Neutral or Normal structure
        _tp_structures = {"Neutral", "Neutral Extreme", "Normal", "Normal Variation"}
        _label_base    = label.split(" ")[0] if label else ""
        _tp_triggered  = (
            avg_entry > 0
            and ib_high is not None
            and price_now >= ib_high
            and any(s in label for s in _tp_structures)
        )
        if _tp_triggered:
            _mfe      = st.session_state.position_peak_price   # capture before exit clears it
            _realized = exit_position(price_now, actual_structure=label)
            _pnl_col  = "#4caf50" if _realized >= 0 else "#ef5350"
            st.markdown(
                f'<div style="background:{_pnl_col}22; border:1px solid {_pnl_col}; '
                f'border-radius:6px; padding:10px 18px; margin:6px 0; font-size:14px; '
                f'font-weight:700; color:{_pnl_col};">'
                f'🎯 AUTO TAKE PROFIT — Price reached IB High ${ib_high:.2f} on a '
                f'<em>{label}</em> day. '
                f'Position closed at ${price_now:.2f}. '
                f'Realized: ${_realized:+.2f} | MFE: ${_mfe:.2f}</div>',
                unsafe_allow_html=True,
            )
            time.sleep(0.5)
            st.rerun()

    # ── Target zone alerts + sidebar "Distance to Target" ─────────────────────
    check_target_alerts(price_now, target_zones, audio_enabled)
    if target_zones:
        with st.sidebar:
            st.markdown("---")
            st.markdown("**🎯 Distance to Target**")
            for tz in target_zones:
                dist_pct = abs(price_now - tz["price"]) / price_now * 100
                arrow = "▲" if tz["price"] > price_now else "▼"
                tc = tz["color"]
                st.markdown(
                    f'<div style="background:#1a2744; border-left:3px solid {tc}; '
                    f'border-radius:4px; padding:6px 10px; margin:4px 0; font-size:12px;">'
                    f'<span style="color:{tc}; font-weight:700;">{tz["label"]}</span> '
                    f'<span style="color:#ccc;">${tz["price"]:.2f}</span> '
                    f'<span style="color:#888;">{arrow} {dist_pct:.1f}% away</span></div>',
                    unsafe_allow_html=True,
                )

    pos_state = {
        "in":         st.session_state.position_in,
        "avg_entry":  st.session_state.position_avg_entry,
        "peak_price": st.session_state.position_peak_price,
        "shares":     st.session_state.position_shares,
    }
    fig = build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, chart_title,
                      target_zones=target_zones, position=pos_state)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("📋 Raw Bar Data"):
        disp = df[["open", "high", "low", "close", "volume"]].copy()
        disp.index = disp.index.strftime("%H:%M")
        disp.columns = ["Open", "High", "Low", "Close", "Volume"]
        st.dataframe(disp, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRE-MARKET GAP SCANNER
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_WATCHLIST = (
    "MSTR,COIN,SOFI,LCID,SIRI,SPCE,FFIE,WKHS,NKLA,MVIS,"
    "CLOV,BB,MMAT,SNDL,TLRY,AFRM,UPST,DKNG,BYND,PLTR,"
    "RIVN,CHPT,BLNK,ASTS,ACHR,JOBY,HOOD,OPEN,PSFE,NRDS"
)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN AREA
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("🔑 Alpaca Credentials")
    api_key = st.text_input("API Key", type="password", placeholder="Alpaca API Key")
    secret_key = st.text_input("Secret Key", type="password", placeholder="Alpaca Secret Key")

    st.markdown("---")
    mode = st.radio("Mode", ["📅 Historical", "🎬 Replay", "🔴 Live Stream"], index=0)

    st.markdown("---")
    st.header("📈 Settings")
    _pending_ticker = st.session_state.get("_load_ticker", "")
    if _pending_ticker:
        st.session_state["ticker_input"] = _pending_ticker
        try:
            del st.session_state["_load_ticker"]
        except Exception:
            st.session_state["_load_ticker"] = ""
    ticker = st.text_input("Ticker Symbol", key="ticker_input",
                           placeholder="e.g. AAPL, GME").upper().strip()
    num_bins = st.slider("Volume Profile Bins", min_value=20, max_value=200, value=100, step=10)
    sector_etf = st.selectbox(
        "Sector ETF (for Tailwind)",
        ["IWM", "XBI", "SMH", "QQQ", "SPY", "XLF", "XLE"],
        index=0,
        help="If this ETF is up > 1% on the day, TCS gets a +10 pt Sector Tailwind bonus."
    )

    run_button = start_live = stop_live = scan_button = replay_load = False
    selected_date = date.today()
    data_feed = "sip"
    watchlist_raw = ""
    scan_feed = "iex"

    if mode == "📅 Historical":
        today = date.today()
        # Default to today if it's a weekday, otherwise roll back to last Friday
        if today.weekday() < 5:
            def_d = today
        elif today.weekday() == 5:   # Saturday → Friday
            def_d = today - timedelta(days=1)
        else:                         # Sunday → Friday
            def_d = today - timedelta(days=2)
        selected_date = st.date_input("Trading Date", value=def_d, max_value=today,
                                       help="Pick a weekday (Mon–Fri). Today's intraday data is supported.")
        data_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX = free, works on all accounts. SIP = full tape, requires a paid Alpaca data subscription.")
        run_button = st.button("🚀 Fetch & Analyze", use_container_width=True, type="primary")

    elif mode == "🎬 Replay":
        today = date.today()
        if today.weekday() < 5:
            def_d = today
        elif today.weekday() == 5:
            def_d = today - timedelta(days=1)
        else:
            def_d = today - timedelta(days=2)
        selected_date = st.date_input("Trading Date", value=def_d, max_value=today,
                                       help="Pick a trading day to replay.", key="replay_date_input")
        data_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX = free. SIP = full tape, needs subscription.", key="replay_feed_sel")
        replay_load = st.button("📥 Load Day for Replay", use_container_width=True, type="primary")

        # ── Replay controls (shown once bars are loaded) ───────────────────────
        if st.session_state.replay_bars is not None:
            _rb      = st.session_state.replay_bars
            _max_idx = len(_rb) - 1
            _cur_idx = st.session_state.replay_bar_idx

            # Current bar's ET time
            _cur_ts  = _rb.index[_cur_idx]
            _cur_et  = pd.Timestamp(_cur_ts).tz_convert(EASTERN)
            _total_et = pd.Timestamp(_rb.index[_max_idx]).tz_convert(EASTERN)

            st.markdown(
                f'<div style="background:#16213e; border:1px solid #5c6bc0; border-radius:6px; '
                f'padding:8px 14px; margin:6px 0; font-family:monospace; font-size:16px; '
                f'color:#90caf9; text-align:center; letter-spacing:1px;">'
                f'🕐 {_cur_et.strftime("%H:%M")} ET &nbsp;|&nbsp; '
                f'Bar {_cur_idx + 1} / {_max_idx + 1}</div>',
                unsafe_allow_html=True,
            )

            # Bar slider
            replay_bar_idx = st.slider(
                "Bar (time)", min_value=0, max_value=_max_idx,
                value=_cur_idx, step=1,
                format="%d",
                key="replay_slider",
            )
            if replay_bar_idx != _cur_idx:
                st.session_state.replay_bar_idx = replay_bar_idx
                st.session_state.replay_playing = False
                st.rerun()

            # Playback controls
            _sp_label = {"Slow (1 bar/step)": 1, "Normal (2 bars/step)": 2,
                         "Fast (5 bars/step)": 5, "Turbo (10 bars/step)": 10}
            _sp_sel = st.selectbox("Speed", list(_sp_label.keys()), index=1,
                                    key="replay_speed_sel")
            st.session_state.replay_speed = _sp_label[_sp_sel]

            rc1, rc2, rc3, rc4 = st.columns(4)
            if rc1.button("⏮", help="Jump to start"):
                st.session_state.replay_bar_idx = 0
                st.session_state.replay_playing = False
                st.rerun()
            if rc2.button("◀", help="Step back"):
                st.session_state.replay_bar_idx = max(0, _cur_idx - st.session_state.replay_speed)
                st.session_state.replay_playing = False
                st.rerun()
            if rc3.button("▶" if not st.session_state.replay_playing else "⏸",
                          help="Play / Pause"):
                st.session_state.replay_playing = not st.session_state.replay_playing
                st.rerun()
            if rc4.button("▶▶", help="Step forward"):
                st.session_state.replay_bar_idx = min(_max_idx, _cur_idx + st.session_state.replay_speed)
                st.session_state.replay_playing = False
                st.rerun()

            if st.button("🗑 Clear / Load new day", use_container_width=True):
                st.session_state.replay_bars    = None
                st.session_state.replay_bar_idx = 0
                st.session_state.replay_playing = False
                st.rerun()

    else:
        live_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX works on all accounts. SIP needs a subscription.")
        if not st.session_state.live_active:
            start_live = st.button("▶ Start Live Stream", use_container_width=True, type="primary")
        else:
            stop_live = st.button("⏹ Stop", use_container_width=True)
            st.success(f"🔴 Live: **{st.session_state.live_ticker}**")

    st.markdown("---")

    # ── Position Management ────────────────────────────────────────────────────
    st.header("📍 Position")
    _snap   = st.session_state.get("last_analysis_state") or {}
    _p_in   = st.session_state.position_in
    _p_tkr  = st.session_state.position_ticker
    _p_ent  = st.session_state.position_avg_entry
    _p_mfe  = st.session_state.position_peak_price
    _p_shr  = st.session_state.position_shares
    _p_strc = st.session_state.position_structure
    _cur    = _snap.get("price", 0.0)

    if _p_in:
        pnl_pct = (_cur - _p_ent) / _p_ent * 100 if _p_ent > 0 else 0
        pnl_dol = (_cur - _p_ent) * _p_shr if _p_shr > 0 else 0
        pnl_col = "#4caf50" if pnl_pct >= 0 else "#ef5350"
        st.markdown(
            f'<div style="background:{pnl_col}11; border:1px solid {pnl_col}55; '
            f'border-radius:6px; padding:10px 14px; margin-bottom:8px;">'
            f'<div style="font-size:11px; color:#888; margin-bottom:4px;">OPEN — {_p_tkr} × {_p_shr} sh</div>'
            f'<div style="font-size:13px; color:#ccc;">Entry: <b>${_p_ent:.2f}</b> &nbsp;|&nbsp; '
            f'MFE: <b>${_p_mfe:.2f}</b></div>'
            f'<div style="font-size:20px; font-weight:800; color:{pnl_col}; margin-top:4px;">'
            f'{"▲" if pnl_pct>=0 else "▼"} {abs(pnl_pct):.2f}%'
            f'{"  ($" + f"{pnl_dol:+.0f}" + ")" if _p_shr > 0 else ""}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        exit_px = st.number_input("Exit Price", value=_cur if _cur else _p_ent,
                                   step=0.01, format="%.2f", key="pos_exit_px")
        exit_struct = st.text_input("Actual Structure (opt.)", value="", key="pos_exit_struct",
                                    placeholder="e.g. Trend Day")
        if st.button("🔴 Exit Position", use_container_width=True, key="pos_exit_btn"):
            _realized = exit_position(exit_px, actual_structure=exit_struct or _p_strc)
            st.success(f"✅ Exited — Realized P&L: ${_realized:+.2f}" if _p_shr > 0
                       else "✅ Position closed.")
            st.rerun()
    else:
        _default_tkr = _snap.get("ticker", "")
        _default_strc = _snap.get("structure", "")
        _default_price = _snap.get("price", 0.0)
        e_tkr = st.text_input("Ticker", value=_default_tkr, key="pos_entry_tkr")
        e_px  = st.number_input("Entry Price", value=float(_default_price) if _default_price else 0.0,
                                 step=0.01, format="%.2f", key="pos_entry_px")
        e_shr = st.number_input("Shares", value=100, step=1, min_value=1, key="pos_entry_shr")
        e_strc = st.text_input("Structure at Entry", value=_default_strc, key="pos_entry_strc")
        if st.button("🟢 Enter Position", use_container_width=True,
                     key="pos_enter_btn", type="primary"):
            if e_tkr and e_px > 0:
                enter_position(e_tkr.upper(), e_px, e_shr, e_strc)
                st.success(f"✅ Entered {e_tkr.upper()} × {e_shr} sh @ ${e_px:.2f}")
                st.rerun()
            else:
                st.error("Enter a valid ticker and price.")

    st.markdown("---")

    # ── Audio alert controls ───────────────────────────────────────────────────
    st.header("🔔 Alerts")
    audio_alerts_enabled = st.checkbox(
        "Enable Audio Alerts",
        value=True,
        key="audio_alerts_enabled",
        help="Play sounds when TCS crosses 80% (chime) or drops below 30% (low tone)."
    )

    if audio_alerts_enabled:
        import streamlit.components.v1 as _comp
        _comp.html(
            """
            <style>
              .ab{background:#16213e;border:1px solid #5c6bc0;color:#aaa;
                  padding:5px 12px;border-radius:5px;cursor:pointer;
                  font-size:12px;width:100%;margin:2px 0;transition:all 0.2s;}
              .ab:hover{border-color:#90caf9;color:#e0e0e0;}
              .ab.ok{border-color:#4caf50!important;color:#4caf50!important;}
              small{color:#555;font-size:10px;line-height:1.4;display:block;margin-top:4px;}
            </style>
            <button class="ab" id="ab" onclick="
              var C=new(window.AudioContext||window.webkitAudioContext)();
              var o=C.createOscillator(),g=C.createGain();
              o.type='sine'; o.frequency.value=880;
              o.connect(g); g.connect(C.destination);
              g.gain.setValueAtTime(0.12,C.currentTime);
              g.gain.exponentialRampToValueAtTime(0.001,C.currentTime+0.3);
              o.start(); o.stop(C.currentTime+0.3);
              this.textContent='✓ Audio Ready';
              this.classList.add('ok');
            ">🔊 Enable Browser Audio</button>
            <small>Browsers block auto-play until you click above once per session.</small>
            """,
            height=68,
            scrolling=False,
        )

    st.markdown("---")

    # ── Gap Scanner Controls ───────────────────────────────────────────────────
    st.header("🔍 Gap Scanner")
    st.caption("Enter tickers to watch. Scan fetches pre-market volume and gap data.")
    watchlist_raw = st.text_area(
        "Watchlist (comma-separated)",
        value=_DEFAULT_WATCHLIST,
        height=110,
        help="Tickers priced $1–$50 at scan time will be analysed.",
        key="watchlist_raw",
    )
    scan_feed = st.selectbox("Scanner Feed", ["iex", "sip"], index=0, key="scan_feed_select",
                             help="IEX = free tier (gap % only). SIP = full tape with pre-market vol (paid subscription).")
    if scan_feed == "iex":
        st.info("ℹ️ IEX (free tier): scanner shows **Gap %** ranked results. "
                "PM Volume will be blank. Upgrade to SIP for pre-market RVOL.")
    scan_button = st.button("🔍 Scan Gap Plays", use_container_width=True)

    st.markdown("---")
    st.caption("SIP = full national tape + pre-market data. IEX = regular hours (9:30–4 PM) only.")



st.title("📊 Volume Profile Dashboard — Small Cap Stocks")

# ── Live Pulse Header ──────────────────────────────────────────────────────────
_las = st.session_state.get("last_analysis_state")
if _las:
    _lbl  = _las.get("structure", "")
    _tcs  = _las.get("tcs", 0.0)
    _rvol = _las.get("rvol")
    _sym  = _las.get("ticker", "")
    _pr   = _las.get("price", 0.0)
    _lc   = _las.get("label_color", "#90caf9")
    _rc   = _las.get("rvol_color", "#aaa")
    _runner = _las.get("is_runner", False)

    _rvol_str = f"{_rvol:.1f}×" if _rvol is not None else "—"
    _tcs_fill = ("linear-gradient(90deg,#FFD700,#00BFFF)" if _runner
                 else f"linear-gradient(90deg,#4caf50,#4caf50)" if _tcs >= 70
                 else f"linear-gradient(90deg,#ef5350,#ef5350)" if _tcs <= 30
                 else "linear-gradient(90deg,#ffa726,#ffa726)")

    st.markdown(f"""
    <div style="display:flex; gap:16px; flex-wrap:wrap; margin:0 0 4px 0;">
        <div style="flex:1; min-width:220px; background:linear-gradient(135deg,{_lc}22,{_lc}0a);
                    border-left:4px solid {_lc}; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:4px;">Structure</div>
            <div style="font-size:20px; font-weight:800; color:{_lc};">{_lbl}</div>
            <div style="font-size:12px; color:#aaa; margin-top:2px;">{_sym} · ${_pr:.2f}</div>
        </div>
        <div style="flex:1; min-width:180px; background:#12122288;
                    border-left:4px solid #90caf9; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:6px;">Trend Confidence (TCS)</div>
            <div style="background:#2a2a4a; border-radius:6px; height:10px; overflow:hidden; margin-bottom:6px;">
                <div style="width:{min(_tcs,100):.0f}%; background:{_tcs_fill};
                            height:100%; border-radius:6px;"></div>
            </div>
            <div style="font-size:22px; font-weight:900; color:{'#FFD700' if _runner else '#90caf9'};">{_tcs:.0f}%</div>
        </div>
        <div style="flex:1; min-width:180px; background:#12122288;
                    border-left:4px solid {_rc}; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:4px;">RVOL</div>
            <div style="font-size:22px; font-weight:900; color:{_rc};">{_rvol_str}</div>
            <div style="font-size:11px; color:#666; margin-top:2px;">
                {'⚡ RUNNER MODE' if _runner else ('🔥 In Play' if _rvol and _rvol > 3 else '— Normal')}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Alert Banner
    if _runner or _tcs >= 80:
        st.markdown(
            '<div style="background:#FFD70022; border:1px solid #FFD700; border-radius:6px; '
            'padding:8px 18px; font-size:14px; font-weight:700; color:#FFD700; margin:4px 0 8px 0;">'
            f'🚀 STOCK IN PLAY — {_sym} | TCS {_tcs:.0f}% | RVOL {_rvol_str}</div>',
            unsafe_allow_html=True
        )
    elif _tcs <= 30:
        st.markdown(
            '<div style="background:#ef535022; border:1px solid #ef5350; border-radius:6px; '
            'padding:8px 18px; font-size:14px; font-weight:700; color:#ef5350; margin:4px 0 8px 0;">'
            f'⚠ CAUTION — Low Conviction | TCS {_tcs:.0f}% | Chop Risk Active</div>',
            unsafe_allow_html=True
        )

_STRUCTURE_COLORS_MAP = {
    "trend":       "#ff9800",
    "bear":        "#ff5722",
    "double":      "#00bcd4",
    "non":         "#78909c",
    "normal var":  "#aed581",
    "variation":   "#aed581",
    "neutral ext": "#7e57c2",
    "neutral":     "#80cbc4",
    "normal":      "#66bb6a",
    "balanced":    "#66bb6a",
}

def _structure_color(label_str):
    """Return a color for a structure label string."""
    s = label_str.lower()
    for key, col in _STRUCTURE_COLORS_MAP.items():
        if key in s:
            return col
    return "#5c6bc0"

def _clean_structure_label(raw):
    """Strip emojis + extra words for a readable short label."""
    import re
    s = re.sub(r"[^\w\s()/\-]", "", str(raw)).strip()
    # Trim very long labels
    return s[:30] if len(s) > 30 else s


_BATCH_DEFAULT = """\
3/30: ANNA, SST, ASTC, BFRG, UGRO, JCSE, EEIQ, ELAB
3/27: VSA, ARTL, GVH, GCTK
3/26: AIFF, EEIQ, GLND, FCHL, VSA
3/25: VCX, RMSG, UGRO, SATL, CODX, QNRX, FEED
3/24: SATL, FEED, RBNE, PAVS, VCX, UGRO, ANNA
3/23: AHMA, UGRO, VCX, BIAF, PTLE
3/20: ARTL, ANNA, CODX
3/19: LNKS, SWMR, ACXP, GOAI, SER, VCX, CHNR
3/18: ARTL, AIM, SWMR, MTVA
3/17: UCAR, LNAI, BIAF, CREG, EDSA, SWMR
3/16: WNW, HCWB"""


def render_tracker_tab():
    """Render the Accuracy Tracker tab — structure distribution + Predicted vs Actual history."""
    st.markdown("## 🧠 MarketBrain — Accuracy Tracker")
    st.caption("All-time structure distribution and brain prediction accuracy.")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 0 — Batch Backtest Runner
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("🔬 Batch Backtest — Feed Historical Tickers", expanded=False):
        st.caption(
            "Fetches each ticker/date pair via Alpaca, classifies structure, records Brain "
            "prediction vs actual. Uses credentials entered in the sidebar. "
            "Skips any pair already in the tracker. Add more lines in M/D: T1, T2 format."
        )
        _bt_pairs_text = st.text_area(
            "Ticker / Date pairs", value=_BATCH_DEFAULT, height=220, key="bt_pairs_input"
        )
        _bt_feed = st.selectbox(
            "Data feed", ["iex", "sip"], index=0, key="bt_feed_select",
            help="IEX = free tier (recommended for backtests). SIP = full tape."
        )
        _bt_run = st.button("▶ Run Batch Backtest", type="primary",
                            use_container_width=True, key="bt_run_btn")

        if _bt_run:
            # api_key and secret_key come from the outer sidebar scope
            if not api_key or not secret_key:
                st.error("Enter your Alpaca API Key and Secret Key in the sidebar first.")
            else:
                _pairs = _parse_batch_pairs(_bt_pairs_text)
                if not _pairs:
                    st.warning("No valid ticker/date pairs found. Use format: 3/30: ANNA, SST, BFRG")
                else:
                    st.info(f"Processing {len(_pairs)} pairs… this may take 1–2 minutes.")
                    _prog   = st.progress(0.0, text="Starting…")
                    _bt_results = []
                    for _i, (_tk, _dt) in enumerate(_pairs):
                        _prog.progress((_i + 1) / len(_pairs),
                                       text=f"Fetching {_tk} {_dt} ({_i+1}/{len(_pairs)})…")
                        _r = run_single_backtest(api_key, secret_key, _tk, _dt,
                                                 feed=_bt_feed, num_bins=100)
                        _bt_results.append(_r)
                    _prog.empty()
                    # Persist results so they survive the page rerun
                    st.session_state["bt_last_results"] = _bt_results

        # ── Always render results if available ────────────────────────────────
        if st.session_state.get("bt_last_results"):
            _rdf = pd.DataFrame(st.session_state["bt_last_results"])
            _ok      = (_rdf["status"] == "OK").sum()
            _dup     = (_rdf["status"] == "Already logged").sum()
            _no_data = _rdf["status"].str.startswith("No data").sum()
            _no_ib   = _rdf["status"].str.startswith("IB").sum()
            _errs    = len(_rdf) - _ok - _dup - _no_data - _no_ib
            _correct = (_rdf["correct"] == "✅").sum()
            _wrong   = (_rdf["correct"] == "❌").sum()
            _logged  = _ok + _dup

            _acc_str = (f"  •  Batch accuracy: **{_correct}/{_correct+_wrong}** "
                        f"({_correct/((_correct+_wrong) or 1)*100:.0f}%)"
                        if (_correct + _wrong) > 0 else "")
            st.success(
                f"✅ **{_ok}** new logged  •  "
                f"🔁 **{_dup}** already existed  •  "
                f"📭 **{_no_data}** no data  •  "
                f"⏱ **{_no_ib}** IB incomplete  •  "
                f"⚠️ **{_errs}** errors"
                + _acc_str
            )
            st.caption(
                "💡 'No data' means Alpaca IEX doesn't carry historical minute bars for that "
                "ticker on that date. Very small OTC stocks often only appear on SIP. "
                "Try switching the feed to **sip** if you have an Alpaca paid subscription."
            )

            # Color rows by result
            def _bt_style(row):
                s = row.get("correct", "—")
                if s == "✅":  return ["background-color:#4caf5022"] * len(row)
                if s == "❌":  return ["background-color:#ef535022"] * len(row)
                return [""] * len(row)

            try:
                st.dataframe(
                    _rdf[["ticker","date","predicted","actual","correct","status"]]
                      .style.apply(_bt_style, axis=1),
                    use_container_width=True, hide_index=True
                )
            except Exception:
                st.dataframe(
                    _rdf[["ticker","date","predicted","actual","correct","status"]],
                    use_container_width=True, hide_index=True
                )
            if st.button("🗑 Clear batch results", key="bt_clear_btn"):
                st.session_state.pop("bt_last_results", None)
                st.rerun()

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 0b — High Conviction Watchlist (top prob ≥ 80%)
    # ══════════════════════════════════════════════════════════════════════════
    _STRUCT_COLORS_HC = {
        "Trend":        "#ff9800",
        "Dbl Dist":     "#00bcd4",
        "Non-Trend":    "#78909c",
        "Normal":       "#66bb6a",
        "Nrml Var":     "#aed581",
        "Neutral":      "#80cbc4",
        "Ntrl Extreme": "#7e57c2",
    }

    st.markdown("### 🎯 High Conviction Calls  <span style='font-size:13px;color:#888;font-weight:400'>≥ 75% structure probability</span>",
                unsafe_allow_html=True)
    _hc_df = load_high_conviction_log()

    if _hc_df.empty:
        st.info(
            "No high-conviction calls logged yet. "
            "Run a historical or live analysis on any ticker — any day where the "
            "top structure probability ≥ 75% is automatically captured here."
        )
    else:
        # ── Summary pills row ─────────────────────────────────────────────────
        _hc_total   = len(_hc_df)
        _hc_structs = _hc_df["structure"].value_counts()
        _pill_html  = ""
        for _s, _cnt in _hc_structs.items():
            _c = _STRUCT_COLORS_HC.get(_s, "#888888")
            _pill_html += (
                f'<span style="background:{_c}33;color:{_c};border:1px solid {_c}66;'
                f'border-radius:12px;padding:3px 10px;margin:3px;'
                f'font-size:13px;font-weight:600;display:inline-block;">'
                f'{_s} ({_cnt})</span>'
            )
        st.markdown(
            f'<div style="margin-bottom:8px;">'
            f'<b style="color:#ccc;">{_hc_total} total</b> &nbsp;·&nbsp; '
            + _pill_html + "</div>",
            unsafe_allow_html=True,
        )

        # ── Table ─────────────────────────────────────────────────────────────
        def _hc_row_style(row):
            _c = _STRUCT_COLORS_HC.get(row.get("structure", ""), "#888888")
            return [f"background-color:{_c}18;"] * len(row)

        _hc_display = _hc_df[["ticker","date","structure","prob_pct",
                               "ib_high","ib_low","poc_price"]].copy()
        _hc_display.columns = ["Ticker","Date","Structure","Probability %",
                                "IB High","IB Low","POC"]
        _hc_display["Probability %"] = _hc_display["Probability %"].apply(
            lambda x: f"{x:.1f}%"
        )

        try:
            st.dataframe(
                _hc_display.style.apply(_hc_row_style, axis=1),
                use_container_width=True, hide_index=True
            )
        except Exception:
            st.dataframe(_hc_display, use_container_width=True, hide_index=True)

        c1, c2 = st.columns([3, 1])
        with c2:
            if st.button("🗑 Clear list", key="hc_clear_btn"):
                try:
                    os.remove(HICONS_FILE)
                except Exception:
                    pass
                st.rerun()

    st.markdown("---")
    df = load_accuracy_tracker()

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — All-Time Structure Distribution
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 📊 All-Time Structure Distribution")
    st.caption("Every structure classified since you started using the dashboard (from accuracy tracker log).")

    if df.empty or "actual" not in df.columns:
        st.info("No data yet — run analyses to start populating the distribution.")
    else:
        _dist = df["actual"].dropna()
        _dist = _dist[_dist.str.strip() != ""]

        if _dist.empty:
            st.info("No actual structure data logged yet.")
        else:
            # Count + percent
            _counts = _dist.value_counts().reset_index()
            _counts.columns = ["structure", "count"]
            _counts["pct"] = (_counts["count"] / _counts["count"].sum() * 100).round(1)
            _counts["label_clean"] = _counts["structure"].apply(_clean_structure_label)
            _counts["color"] = _counts["structure"].apply(_structure_color)
            _counts = _counts.sort_values("pct", ascending=True)   # horizontal bar → ascending

            # ── Pill badges row ────────────────────────────────────────────────
            pills_html = '<div style="display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 14px 0;">'
            for _, row in _counts.sort_values("pct", ascending=False).iterrows():
                c = row["color"]
                pills_html += (
                    f'<span style="background:{c}22; border:1px solid {c}55; border-radius:20px; '
                    f'padding:4px 12px; font-size:12px; color:{c}; white-space:nowrap;">'
                    f'<b>{row["pct"]:.0f}%</b> {row["label_clean"]} '
                    f'<span style="color:#555; font-size:10px;">({int(row["count"])})</span></span>'
                )
            pills_html += "</div>"
            st.markdown(pills_html, unsafe_allow_html=True)

            # ── Horizontal bar chart ──────────────────────────────────────────
            fig_dist = go.Figure(go.Bar(
                x=_counts["pct"],
                y=_counts["label_clean"],
                orientation="h",
                marker_color=_counts["color"].tolist(),
                text=[f"  {p:.1f}%  ({n})" for p, n in zip(_counts["pct"], _counts["count"])],
                textposition="outside",
                cliponaxis=False,
            ))
            fig_dist.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"),
                height=max(240, len(_counts) * 44 + 60),
                xaxis=dict(range=[0, min(100, _counts["pct"].max() * 1.25)],
                           gridcolor="#2a2a4a", title="% of all sessions",
                           ticksuffix="%"),
                yaxis=dict(gridcolor="#2a2a4a", tickfont=dict(size=12)),
                margin=dict(l=10, r=80, t=20, b=40),
            )
            st.plotly_chart(fig_dist, use_container_width=True)

            # ── Trend vs Balance split ────────────────────────────────────────
            _directional_keys = ["trend", "bear", "double", "variation"]
            _balanced_keys    = ["normal", "neutral", "non", "balanced"]

            def _classify_side(s):
                sl = s.lower()
                if any(k in sl for k in _directional_keys):
                    return "Directional"
                if any(k in sl for k in _balanced_keys):
                    return "Balanced"
                return "Other"

            _sides = _dist.apply(_classify_side).value_counts()
            _total_sides = _sides.sum()
            dir_pct = _sides.get("Directional", 0) / _total_sides * 100
            bal_pct = _sides.get("Balanced", 0) / _total_sides * 100

            d1, d2, d3 = st.columns(3)
            d1.metric("📈 Directional Days", f"{dir_pct:.0f}%",
                      help="Trend Day, Trend Bear, Double Distribution, Normal Variation")
            d2.metric("⚖️ Balanced Days",    f"{bal_pct:.0f}%",
                      help="Normal, Neutral, Neutral Extreme, Non-Trend")
            d3.metric("📋 Total Sessions",   int(_total_sides))

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — Brain Accuracy
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 🎯 Brain Prediction Accuracy")

    if df.empty:
        st.info("No accuracy data yet — run analyses after 10:30 ET to start logging brain predictions.")
        return

    total    = len(df)
    correct  = (df["correct"] == "✅").sum()
    acc_rate = correct / total * 100 if total > 0 else 0
    wrong    = total - correct

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Predictions", total)
    c2.metric("Correct", f"{correct}  ({acc_rate:.0f}%)")
    c3.metric("Wrong",   wrong)
    c4.metric("Accuracy Rate", f"{acc_rate:.1f}%")

    # ── Accuracy by predicted structure ──────────────────────────────────────
    if "predicted" in df.columns and "correct" in df.columns:
        grouped = df.groupby("predicted").apply(
            lambda g: pd.Series({
                "total":   len(g),
                "correct": (g["correct"] == "✅").sum(),
                "acc":     (g["correct"] == "✅").sum() / len(g) * 100,
            })
        ).reset_index()
        grouped = grouped.sort_values("acc", ascending=False)

        st.markdown("**Accuracy by Predicted Structure**")
        bar_colors = [
            "#4caf50" if a >= 60 else "#ffa726" if a >= 40 else "#ef5350"
            for a in grouped["acc"]
        ]
        fig_acc = go.Figure(go.Bar(
            x=grouped["predicted"].apply(_clean_structure_label),
            y=grouped["acc"].round(1),
            marker_color=bar_colors,
            text=[f"{a:.0f}%<br>({int(c)}/{int(t)})"
                  for a, c, t in zip(grouped["acc"], grouped["correct"], grouped["total"])],
            textposition="outside",
        ))
        fig_acc.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"), height=320,
            yaxis=dict(range=[0, 115], gridcolor="#2a2a4a", title="Accuracy %"),
            xaxis=dict(gridcolor="#2a2a4a"),
            margin=dict(t=20, b=60, l=50, r=20),
        )
        st.plotly_chart(fig_acc, use_container_width=True)

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — Adaptive Learning Status
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 🔬 Adaptive Learning Status")
    st.caption(
        f"Brain recalibrates its probability weights every {_RECALIBRATE_EVERY} comparisons. "
        f"Structures with ≥5 samples are eligible. Multiplier > 1.0 = trusted; < 1.0 = confidence reduced."
    )

    _ws_rows = brain_weights_summary()
    _raw_w   = load_brain_weights()

    if not _ws_rows:
        st.info(f"Learning begins once at least 5 comparisons are logged for any structure. "
                f"Full recalibration fires every {_RECALIBRATE_EVERY} entries.")
    else:
        # ── Weight bar chart ──────────────────────────────────────────────────
        _wdf = pd.DataFrame(_ws_rows)
        _bar_colors = [
            "#4caf50" if m >= 1.3 else "#26a69a" if m >= 1.0 else
            "#ffa726" if m >= 0.7 else "#ef5350"
            for m in _wdf["Multiplier"]
        ]
        fig_w = go.Figure(go.Bar(
            x=_wdf["Multiplier"],
            y=_wdf["Structure"].apply(_clean_structure_label),
            orientation="h",
            marker_color=_bar_colors,
            text=[f"  {m:.2f}×  ({a:.0f}% acc / {n} samples)"
                  for m, a, n in zip(_wdf["Multiplier"], _wdf["Accuracy"], _wdf["Samples"])],
            textposition="outside",
            cliponaxis=False,
        ))
        fig_w.add_vline(x=1.0, line_dash="dash", line_color="#5c6bc0",
                        annotation_text="Baseline", annotation_position="top")
        fig_w.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"),
            height=max(220, len(_wdf) * 44 + 60),
            xaxis=dict(range=[0.0, 1.8], gridcolor="#2a2a4a",
                       title="Probability Multiplier"),
            yaxis=dict(gridcolor="#2a2a4a"),
            margin=dict(l=10, r=120, t=20, b=40),
        )
        st.plotly_chart(fig_w, use_container_width=True)

        # ── Summary table ──────────────────────────────────────────────────────
        _wdf_disp = _wdf[["Structure", "Samples", "Accuracy", "Multiplier", "Status"]].copy()
        _wdf_disp["Accuracy"] = _wdf_disp["Accuracy"].apply(lambda x: f"{x:.1f}%")
        _wdf_disp["Multiplier"] = _wdf_disp["Multiplier"].apply(lambda x: f"{x:.3f}×")
        st.dataframe(_wdf_disp, use_container_width=True, hide_index=True)

        # ── Next recalibration countdown ──────────────────────────────────────
        _current_n = len(df) if not df.empty else 0
        _next_recal = _RECALIBRATE_EVERY - (_current_n % _RECALIBRATE_EVERY)
        if _next_recal == _RECALIBRATE_EVERY:
            st.success(f"✅ Weights just recalibrated at {_current_n} entries.")
        else:
            _entry_word = "entry" if _next_recal == 1 else "entries"
            st.info(f"🔄 Next recalibration in **{_next_recal}** more {_entry_word} "
                    f"({_current_n} logged so far).")

        # ── Manual recalibrate button ─────────────────────────────────────────
        if st.button("⚡ Recalibrate Now", help="Force immediate weight update from all logged data"):
            _new_w = recalibrate_brain_weights()
            st.success("Weights updated! Brain probabilities will use the new calibration on next analysis.")
            st.rerun()

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — Full history table
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 📋 Full History")
    display_cols = ["timestamp", "symbol", "predicted", "actual", "correct",
                    "entry_price", "exit_price", "mfe"]
    disp = df[[c for c in display_cols if c in df.columns]].copy()
    if "timestamp" in disp.columns:
        disp = disp.sort_values("timestamp", ascending=False)

    def _style_row(row):
        color = "#4caf5022" if row.get("correct") == "✅" else "#ef535022"
        return [f"background-color: {color}"] * len(row)

    try:
        styled = disp.style.apply(_style_row, axis=1)
        st.dataframe(styled, use_container_width=True, height=320)
    except Exception:
        st.dataframe(disp, use_container_width=True, height=320)

    csv_str = df.to_csv(index=False)
    st.download_button(
        "⬇ Download Tracker CSV", data=csv_str,
        file_name="accuracy_tracker.csv", mime="text/csv"
    )


# ══════════════════════════════════════════════════════════════════════════════
# SMALL ACCOUNT CHALLENGE TAB
# ══════════════════════════════════════════════════════════════════════════════

def render_sa_tab():
    """Small Account Challenge — Ross Cameron / Warrior Trading methodology.
    Five Pillars | Sniper Entry | Position Sizer | PDT Tracker | Recovery Ratio
    """
    now_et   = datetime.now(EASTERN)
    hour_et  = now_et.hour + now_et.minute / 60.0
    snap     = st.session_state.get("last_analysis_state") or {}
    bars     = st.session_state.get("last_bars")
    ticker   = snap.get("ticker", "")
    price    = snap.get("price", 0.0)
    rvol     = snap.get("rvol")
    ib_high  = snap.get("ib_high")
    ib_low   = snap.get("ib_low")
    poc_p    = snap.get("poc_price")
    val_p    = snap.get("val_price")
    vah_p    = snap.get("vah_price")
    pct_chg  = snap.get("pct_change", 0.0)

    # ── SECTION 1 — TRADING WINDOW + PDT TRACKER ─────────────────────────────
    window_open = 7.0 <= hour_et < 10.0
    mins_left   = max(0, int((10.0 - hour_et) * 60)) if window_open else 0
    if window_open:
        wc, wlbl = "#4caf50", f"✅  SNIPER WINDOW ACTIVE — {mins_left} min remaining"
        wbg = "#1b5e2022"
    elif hour_et < 7.0:
        wc, wlbl = "#ffa726", f"⏳  Pre-market — Window opens at 7:00 AM ET"
        wbg = "#e6510022"
    else:
        wc, wlbl = "#ef5350", "❌  WINDOW CLOSED — Trades after 10 AM carry lower win rate"
        wbg = "#4e111122"

    wcol1, wcol2 = st.columns([3, 1])
    with wcol1:
        st.markdown(
            f'<div style="background:{wbg};border:1px solid {wc};border-radius:8px;'
            f'padding:10px 20px;margin-bottom:8px;">'
            f'<span style="font-size:14px;font-weight:700;color:{wc};">{wlbl}</span>'
            f'<span style="font-size:12px;color:#666;margin-left:18px;">'
            f'{now_et.strftime("%H:%M:%S ET")}</span></div>',
            unsafe_allow_html=True,
        )
    with wcol2:
        pdt_used = st.number_input(
            "Day trades used (this week)", min_value=0, max_value=10,
            value=int(st.session_state.get("sa_pdt_used", 0)),
            step=1, key="sa_pdt_inp",
            help="PDT rule: ≤3 round-trip day trades in 5 days for accounts under $25k"
        )
        st.session_state.sa_pdt_used = pdt_used
        pdt_rem   = max(0, 3 - pdt_used)
        pdt_color = "#ef5350" if pdt_rem == 0 else "#ffa726" if pdt_rem == 1 else "#4caf50"
        st.markdown(
            f'<div style="font-size:18px;font-weight:800;color:{pdt_color};">'
            f'{pdt_rem}/3 trades left</div>'
            + ('<div style="font-size:11px;color:#ef5350;">⛔ PDT LIMIT — no more day trades</div>'
               if pdt_rem == 0 else ''),
            unsafe_allow_html=True,
        )

    # ── SECTION 2 — FIVE PILLARS EVALUATION ──────────────────────────────────
    st.markdown("---")
    st.markdown("### 🏛️ Five Pillars Evaluation")

    if not ticker:
        st.info("Run a Historical Analysis on the Main Chart tab first — "
                "the Five Pillars will auto-populate for the loaded ticker.")
    else:
        c_float, c_news = st.columns(2)
        with c_float:
            float_est = st.number_input(
                "Float estimate (M shares) — check Finviz / Benzinga",
                min_value=0.0, max_value=1000.0,
                value=float(st.session_state.get("sa_float_est", 0.0)),
                step=0.5, format="%.1f", key="sa_float_inp",
            )
            st.session_state.sa_float_est = float_est
        with c_news:
            news_ok = st.checkbox(
                "✅  Catalyst confirmed (FDA, 13D filing, PR, earnings beat, etc.)",
                value=st.session_state.get("sa_news_confirmed", False),
                key="sa_news_chk",
            )
            st.session_state.sa_news_confirmed = news_ok

        # Evaluate pillars
        p_price = 0.25 <= price <= 9.00
        p_chg   = pct_chg >= 50.0
        p_rvol  = (rvol is not None and rvol >= 100.0)
        p_news  = news_ok
        p_f5    = float_est > 0 and float_est <= 5.0
        p_f20   = float_est > 0 and float_est <= 20.0
        p_float = p_f5 or p_f20

        float_note = (
            f"<span style='color:#4caf50;'>Sub-5M ✅ Explosive potential</span> — {float_est:.1f}M" if p_f5 else
            f"<span style='color:#ffa726;'>Sub-20M ⚠️ Acceptable max</span> — {float_est:.1f}M" if p_f20 else
            f"<span style='color:#ef5350;'>&gt;20M ❌ Too big — Algos dominant</span> — {float_est:.1f}M" if float_est > 0 else
            "<span style='color:#555;'>Enter float estimate above</span>"
        )

        def _pill_row(icon, passed, name, note, warn=False):
            c = "#4caf50" if passed else ("#ffa726" if warn else "#ef5350")
            return (
                f'<tr style="border-top:1px solid #1c2040;">'
                f'<td style="padding:6px 10px;font-size:16px;">{icon}</td>'
                f'<td style="padding:6px 10px;font-weight:700;color:{c};white-space:nowrap;">{name}</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#aaa;">{note}</td>'
                f'</tr>'
            )

        score = sum([p_price, p_chg, p_rvol, p_news, p_float])
        sc    = "#4caf50" if score == 5 else "#ffa726" if score >= 3 else "#ef5350"
        slbl  = ("⭐ A+ — ALL PILLARS MET" if score == 5
                 else "B — Borderline" if score >= 3 else "❌ AVOID — Sub-threshold")

        tbl = (
            '<table style="width:100%;border-collapse:collapse;">'
            + _pill_row("✅" if p_price else "❌", p_price, "Price",
                        f"${price:.2f}  &nbsp;·&nbsp; Target: $0.25 – $9.00")
            + _pill_row("✅" if p_chg else "❌", p_chg, "% Change",
                        f"{pct_chg:+.1f}%  &nbsp;·&nbsp; Need ≥ 50%")
            + _pill_row("✅" if p_rvol else "❌", p_rvol, "RVOL",
                        f"{f'{rvol:.0f}×' if rvol else '—'}  &nbsp;·&nbsp; Need ≥ 100× (institutional prints)")
            + _pill_row("✅" if p_news else "⚠️", p_news, "News Catalyst",
                        "FDA / 13D / PR / earnings beat required — momentum without news fades faster",
                        warn=True)
            + _pill_row("✅" if p_float else ("⚠️" if p_f20 else "❌"), p_float, "Float",
                        float_note, warn=(p_f20 and not p_f5))
            + '</table>'
        )
        st.markdown(
            f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:10px;'
            f'padding:14px 18px;margin:8px 0;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">'
            f'<span style="font-size:17px;font-weight:900;color:#e0e0e0;letter-spacing:1px;">{ticker}</span>'
            f'<span style="background:{sc}22;border:1px solid {sc};border-radius:5px;'
            f'padding:4px 14px;font-size:13px;font-weight:800;color:{sc};">{slbl}</span>'
            f'</div>{tbl}</div>',
            unsafe_allow_html=True,
        )

        # Pre-market float turnover (bonus)
        if float_est > 0:
            pm_row = next(
                (r for r in st.session_state.scanner_results if r.get("ticker") == ticker), None)
            if pm_row and pm_row.get("pm_vol"):
                float_sh = float_est * 1_000_000
                pmv      = pm_row["pm_vol"]
                tover    = pmv / float_sh * 100.0 if float_sh > 0 else 0.0
                tc  = "#ef5350" if tover > 80 else "#ffa726" if tover > 40 else "#4caf50"
                tlb = ("EXTREME — Float nearly traded through" if tover > 80
                       else "HIGH — Supply/demand heavily imbalanced" if tover > 40
                       else "Normal pre-market activity")
                st.markdown(
                    f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
                    f'padding:8px 16px;margin:4px 0;font-size:12px;color:#aaa;">'
                    f'📊 PM Float Turnover: <b style="color:{tc};">{tover:.1f}%</b>'
                    f' <span style="color:{tc};">({tlb})</span>'
                    f' — {pmv:,} shares vs ~{float_sh:,.0f} float</div>',
                    unsafe_allow_html=True,
                )

    # ── SECTION 3 — SETUP QUALITY (if bars loaded) ────────────────────────────
    if bars is not None and len(bars) >= 5:
        st.markdown("---")
        st.markdown("### 📡 Setup Quality Analysis")

        atr    = compute_atr(bars)
        greens = count_consecutive_greens(bars)
        mline, sline, hist = compute_macd(bars["close"])
        macd_now  = float(mline.iloc[-1])
        sig_now   = float(sline.iloc[-1])
        hist_now  = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2]) if len(hist) >= 2 else hist_now

        # Front Side: MACD above signal AND histogram not yet shrinking
        macd_above  = macd_now > sig_now
        hist_open   = hist_now > 0 and hist_now >= hist_prev * 0.80
        front_side  = macd_above and hist_open

        # VA Breakout
        va_breakout = (vah_p is not None and price is not None
                       and price > vah_p and rvol is not None and rvol >= 100.0)

        # POC shift
        bin_c = st.session_state.get("sa_bin_centers")
        vap_c = st.session_state.get("sa_vap")
        poc_shift_lbl, poc_shift_col = (
            detect_poc_shift(bin_c, vap_c)
            if bin_c is not None and vap_c is not None and len(bin_c) > 0
            else ("—", "#aaa")
        )

        # Whole/Half dollar levels
        try:
            whl = get_whole_half_levels(float(bars["low"].min()), float(bars["high"].max()))
        except Exception:
            whl = []

        # Opening Range (first 5 bars)
        or_hi = float(bars["high"].iloc[:5].max()) if len(bars) >= 5 else None
        or_lo = float(bars["low"].iloc[:5].min())  if len(bars) >= 5 else None

        qa1, qa2, qa3 = st.columns(3)

        with qa1:
            fsc = "#4caf50" if front_side else "#ef5350"
            fsl = "✅ FRONT SIDE" if front_side else "⛔ BACK SIDE"
            fsb = "MACD open & diverging" if front_side else "MACD converging — fade risk"
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid {fsc}55;border-radius:8px;'
                f'padding:14px;text-align:center;">'
                f'<div style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Trade Side</div>'
                f'<div style="font-size:19px;font-weight:800;color:{fsc};">{fsl}</div>'
                f'<div style="font-size:11px;color:#777;margin-top:3px;">{fsb}</div>'
                f'<div style="font-size:10px;color:#444;margin-top:4px;font-family:monospace;">'
                f'MACD {macd_now:+.4f} · Sig {sig_now:+.4f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with qa2:
            gc  = "#4caf50" if greens >= 3 else "#ffa726" if greens >= 1 else "#ef5350"
            glb = "🔥 SURGE" if greens >= 3 else "Building" if greens >= 1 else "No Surge"
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid {gc}55;border-radius:8px;'
                f'padding:14px;text-align:center;">'
                f'<div style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Consecutive Greens</div>'
                f'<div style="font-size:34px;font-weight:900;color:{gc};line-height:1;">{greens}</div>'
                f'<div style="font-size:12px;color:{gc};margin-top:3px;">{glb}</div>'
                f'<div style="font-size:10px;color:#444;margin-top:4px;">Need 3+ for surge confirmation</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with qa3:
            vac = "#4caf50" if va_breakout else "#555"
            vat = "✅ BREAKING VA on RVOL ≥ 100" if va_breakout else "Inside Value Area"
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:8px;'
                f'padding:14px;text-align:center;">'
                f'<div style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">VA / POC</div>'
                f'<div style="font-size:13px;font-weight:700;color:{poc_shift_col};margin:4px 0;">{poc_shift_lbl}</div>'
                f'<div style="font-size:11px;color:{vac};">{vat}</div>'
                + (f'<div style="font-size:10px;color:#555;margin-top:3px;">VAH ${vah_p:.2f} · VAL ${val_p:.2f}</div>'
                   if vah_p and val_p else '')
                + f'</div>',
                unsafe_allow_html=True,
            )

        # Opening Range + ATR
        if or_hi and or_lo:
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
                f'padding:8px 16px;margin:8px 0;font-size:12px;color:#aaa;">'
                f'📐 Opening Range (first 5 bars): '
                f'<b style="color:#00e676;">Hi ${or_hi:.2f}</b> / '
                f'<b style="color:#ef5350;">Lo ${or_lo:.2f}</b> — '
                f'OR Range: <b style="color:#e0e0e0;">${or_hi - or_lo:.2f}</b>'
                f'&nbsp;&nbsp;|&nbsp;&nbsp;ATR(14): <b style="color:#5c6bc0;">${atr:.2f}</b>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Whole / Half dollar resistance levels
        if whl:
            whole_s = "  ".join(
                f"<b style='color:#ffa726;'>${l:.2f}</b>" if l == int(l)
                else f"<span style='color:#7986cb;'>${l:.2f}</span>"
                for l in whl
            )
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
                f'padding:8px 16px;margin:4px 0;font-size:12px;color:#aaa;">'
                f'🎯 Whole/Half-Dollar Levels: {whole_s}'
                f'&nbsp;&nbsp;<span style="color:#444;font-size:10px;">'
                f'(orange = whole $, blue = half $  — expect resistance, then support once broken)</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── SECTION 4 — SNIPER ENTRY CHECKLIST ───────────────────────────────────
    st.markdown("---")
    st.markdown("### 🎯 Sniper Entry Checklist — First Candle New High")
    st.caption("All 7 boxes must be ✅ before pulling the trigger. One unchecked = wait.")

    ch1, ch2 = st.columns(2)
    with ch1:
        chk1 = st.checkbox("📈 Surge: 3–5 consecutive green candles on volume spike", key="sa_c1")
        chk2 = st.checkbox("🔽 Pullback: 1–2 red candles on notably lighter volume",   key="sa_c2")
        chk3 = st.checkbox("🔺 Apex: Price approaching high of the pullback candle",   key="sa_c3")
        chk4 = st.checkbox("✅ Trigger: Candle BROKE the apex (confirmed — not anticipated)", key="sa_c4")
    with ch2:
        chk5 = st.checkbox("📊 MACD: Lines still open / histogram not shrinking",       key="sa_c5")
        chk6 = st.checkbox("📋 Level 2: Surge of green orders visible on tape",         key="sa_c6")
        chk7 = st.checkbox("🛡️ PDT: Day trade slot available (not at 3-trade limit)",   key="sa_c7",
                            value=(st.session_state.get("sa_pdt_used", 0) < 3))
        entry_type = st.selectbox(
            "Entry Type",
            ["First Pullback", "ABCD Pattern", "High of Day Break", "Other"],
            key="sa_entry_sel",
        )

    checks_passed = sum([chk1, chk2, chk3, chk4, chk5, chk6, chk7])
    if checks_passed == 7:
        st.markdown(
            '<div style="background:#4caf5022;border:2px solid #4caf50;border-radius:8px;'
            'padding:12px 20px;font-weight:700;color:#4caf50;font-size:15px;margin:8px 0;">'
            '🟢 ALL 7 CHECKS PASSED — VALID SNIPER ENTRY</div>',
            unsafe_allow_html=True,
        )
    elif checks_passed >= 5:
        st.markdown(
            f'<div style="background:#ffa72622;border:1px solid #ffa726;border-radius:8px;'
            f'padding:12px 20px;font-weight:700;color:#ffa726;font-size:14px;margin:8px 0;">'
            f'⚠️ {checks_passed}/7 — Partial. Tighten your criteria before entry.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="background:#ef535022;border:1px solid #ef5350;border-radius:8px;'
            f'padding:12px 20px;font-weight:700;color:#ef5350;font-size:14px;margin:8px 0;">'
            f'🔴 {checks_passed}/7 — DO NOT ENTER. Be the sniper, not the machine gunner.</div>',
            unsafe_allow_html=True,
        )

    # ── SECTION 5 — POSITION SIZER ───────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 💰 Sniper Position Sizer")

    ps1, ps2, ps3 = st.columns(3)
    with ps1:
        acct = st.number_input(
            "Account Balance ($)", min_value=100.0, max_value=1_000_000.0,
            value=float(st.session_state.get("sa_account_bal", 5000.0)),
            step=500.0, format="%.0f", key="sa_acct",
        )
        st.session_state.sa_account_bal = acct
        risk_p = st.number_input(
            "Max Risk per Trade (%)", min_value=0.5, max_value=10.0,
            value=float(st.session_state.get("sa_risk_pct", 2.0)),
            step=0.5, format="%.1f", key="sa_riskp",
        )
        st.session_state.sa_risk_pct = risk_p
    with ps2:
        atr_v = compute_atr(bars) if bars is not None and len(bars) >= 5 else 0.20
        stop_d = st.number_input(
            "Stop Distance ($)",
            min_value=0.01, max_value=20.0,
            value=round(max(0.05, atr_v), 2),
            step=0.01, format="%.2f",
            help="ATR(14) auto-filled. Adjust to your actual stop placement.",
            key="sa_stopd",
        )
        st.markdown(
            f'<div style="font-size:11px;color:#5c6bc0;margin-top:4px;">'
            f'ATR(14) auto-fill: ${atr_v:.2f}</div>',
            unsafe_allow_html=True,
        )
    with ps3:
        risk_dol   = acct * (risk_p / 100.0)
        max_shares = int(risk_dol / stop_d) if stop_d > 0 else 0
        max_pos    = max_shares * price if price > 0 else 0.0
        daily_lim  = acct * 0.25
        st.markdown(
            f'<div style="background:#0c1030;border:1px solid #5c6bc0;border-radius:10px;'
            f'padding:14px;text-align:center;">'
            f'<div style="font-size:10px;color:#5c6bc0;text-transform:uppercase;letter-spacing:1px;">Risk Budget</div>'
            f'<div style="font-size:22px;font-weight:800;color:#e0e0e0;">${risk_dol:.0f}</div>'
            f'<div style="font-size:11px;color:#555;margin:4px 0;">Max Shares</div>'
            f'<div style="font-size:30px;font-weight:900;color:#4caf50;line-height:1;">{max_shares:,}</div>'
            f'<div style="font-size:11px;color:#555;">Position size: ${max_pos:,.0f}</div>'
            f'<div style="font-size:11px;color:#ef5350;margin-top:6px;">Daily loss limit: ${daily_lim:,.0f}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
        f'padding:8px 16px;margin:6px 0;font-size:12px;color:#aaa;">'
        f'💡 <b style="color:#e0e0e0;">Cash Account Rule:</b> Treat every trade as your ONLY trade of the day. '
        f'Settle T+1 before sizing up. Sniper — not machine gunner. '
        f'<b style="color:#ef5350;">Stop trading if daily loss hits ${daily_lim:,.0f}.</b>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── SECTION 6 — RECOVERY RATIO ───────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📉 Recovery Ratio — The Math of Drawdown")
    st.caption("A 25% loss requires a 33% gain just to break even. Protect capital first.")

    rr1, rr2 = st.columns([1, 2])
    with rr1:
        loss_in = st.slider("Simulate a loss of:", 1, 90, 25, format="%d%%", key="sa_rrs")
        rr_val  = compute_recovery_ratio(loss_in)
        rrc     = "#4caf50" if loss_in < 15 else "#ffa726" if loss_in < 35 else "#ef5350"
        st.markdown(
            f'<div style="background:#0c1030;border:2px solid {rrc};border-radius:10px;'
            f'padding:16px;text-align:center;margin:8px 0;">'
            f'<div style="font-size:12px;color:#aaa;">After a <b style="color:{rrc};">{loss_in}%</b> loss</div>'
            f'<div style="font-size:11px;color:#555;margin:4px 0;">you need to gain</div>'
            f'<div style="font-size:38px;font-weight:900;color:{rrc};">{rr_val:.1f}%</div>'
            f'<div style="font-size:11px;color:#aaa;">to return to break-even</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with rr2:
        rows_rr = [(10, "Manageable"), (20, "Manageable"), (25, "Dangerous"),
                   (33, "Dangerous"),  (50, "DEATH SPIRAL"), (75, "DEATH SPIRAL")]
        tbl = '<table style="width:100%;border-collapse:collapse;font-size:12px;font-family:monospace;">'
        tbl += ('<tr style="color:#5c6bc0;font-size:10px;text-transform:uppercase;">'
                '<th style="text-align:left;padding:5px 10px;">Loss %</th>'
                '<th style="text-align:left;padding:5px 10px;">Must Gain</th>'
                '<th style="text-align:left;padding:5px 10px;">Verdict</th></tr>')
        for lp, verdict in rows_rr:
            rr_v  = compute_recovery_ratio(lp)
            rc2   = "#4caf50" if lp < 15 else "#ffa726" if lp < 35 else "#ef5350"
            tbl  += (f'<tr style="border-top:1px solid #1c2040;">'
                     f'<td style="padding:5px 10px;color:{rc2};font-weight:700;">−{lp}%</td>'
                     f'<td style="padding:5px 10px;color:{rc2};">+{rr_v:.1f}%</td>'
                     f'<td style="padding:5px 10px;color:{rc2};">{verdict}</td></tr>')
        tbl += '</table>'
        st.markdown(
            f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:8px;'
            f'padding:12px;">{tbl}</div>',
            unsafe_allow_html=True,
        )

    # ── SECTION 7 — SNIPER TRADE LOG ─────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📋 Sniper Trade Log — Cognitive Audit")
    st.caption(
        "The data doesn't lie: confirmed entries (waited for the break) vs. "
        "anticipated entries (jumped early). Track both — your win rate will show the difference."
    )

    with st.expander("➕ Log a Trade", expanded=False):
        lg1, lg2, lg3 = st.columns(3)
        with lg1:
            lg_tick  = st.text_input("Ticker", value=ticker or "", key="sa_lgt").upper()
            lg_ep    = st.number_input("Entry Price", min_value=0.01,
                                        value=float(price or 1.0), step=0.01,
                                        format="%.2f", key="sa_lgep")
            lg_xp    = st.number_input("Exit Price", min_value=0.01,
                                        value=float(price or 1.0), step=0.01,
                                        format="%.2f", key="sa_lgxp")
        with lg2:
            lg_et    = st.selectbox("Entry Type",
                                     ["First Pullback", "ABCD Pattern",
                                      "High of Day Break", "Other"],
                                     key="sa_lget")
            lg_conf  = st.radio("Entry Discipline",
                                 ["✅ Confirmed (waited for break)",
                                  "⚠️ Anticipated (jumped early)"],
                                 key="sa_lgconf")
            lg_news  = st.checkbox("Catalyst confirmed at entry time", key="sa_lgnews")
        with lg3:
            in_win   = window_open
            ts_str   = now_et.strftime("%H:%M ET")
            if not in_win:
                st.warning(f"⚠️ {ts_str} — Outside sniper window")
            else:
                st.success(f"✅ {ts_str} — Inside window")
            lg_notes = st.text_area("Notes / reason for entry", height=90,
                                     placeholder="Why you took this trade…",
                                     key="sa_lgnotes")

        if st.button("💾 Log Trade", key="sa_log_btn"):
            pnl_p = round((lg_xp - lg_ep) / lg_ep * 100, 2) if lg_ep > 0 else 0.0
            entry = {
                "date":        now_et.strftime("%Y-%m-%d"),
                "time":        ts_str,
                "ticker":      lg_tick,
                "entry_price": round(lg_ep, 2),
                "exit_price":  round(lg_xp, 2),
                "pnl_pct":     pnl_p,
                "entry_type":  lg_et,
                "confirmed":   "Confirmed" if "Confirmed" in lg_conf else "Anticipated",
                "news":        "✅" if lg_news else "❌",
                "in_window":   "✅" if in_win else "❌",
                "notes":       lg_notes,
            }
            journal = load_sa_journal()
            journal.append(entry)
            save_sa_journal(journal)
            st.success(f"Logged: {lg_tick} | {pnl_p:+.2f}%")
            st.rerun()

    # Display journal + cognitive audit split
    journal = load_sa_journal()
    if journal:
        dfj = pd.DataFrame(journal)

        # Win-rate by confirmed vs anticipated
        if "confirmed" in dfj.columns and "pnl_pct" in dfj.columns:
            conf_df = dfj[dfj["confirmed"] == "Confirmed"]
            anti_df = dfj[dfj["confirmed"] == "Anticipated"]
            cwr = (conf_df["pnl_pct"] > 0).mean() * 100 if len(conf_df) else None
            awr = (anti_df["pnl_pct"] > 0).mean() * 100 if len(anti_df) else None

            au1, au2 = st.columns(2)
            with au1:
                cwc = "#4caf50" if (cwr or 0) >= 50 else "#ef5350"
                st.markdown(
                    f'<div style="background:#0c1030;border:1px solid {cwc};border-radius:8px;'
                    f'padding:12px;text-align:center;">'
                    f'<div style="font-size:11px;color:#aaa;">✅ Confirmed Entry Win Rate</div>'
                    f'<div style="font-size:28px;font-weight:800;color:{cwc};">'
                    f'{"%.0f%%" % cwr if cwr is not None else "—"}</div>'
                    f'<div style="font-size:10px;color:#555;">{len(conf_df)} trades</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with au2:
                awc = "#4caf50" if (awr or 0) >= 50 else "#ef5350"
                st.markdown(
                    f'<div style="background:#0c1030;border:1px solid {awc};border-radius:8px;'
                    f'padding:12px;text-align:center;">'
                    f'<div style="font-size:11px;color:#aaa;">⚠️ Anticipated Entry Win Rate</div>'
                    f'<div style="font-size:28px;font-weight:800;color:{awc};">'
                    f'{"%.0f%%" % awr if awr is not None else "—"}</div>'
                    f'<div style="font-size:10px;color:#555;">{len(anti_df)} trades</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # Recent trades table
        st.markdown("**Recent Trades**")
        for row in reversed(journal[-15:]):
            pnl = row.get("pnl_pct", 0)
            pc  = "#4caf50" if pnl > 0 else "#ef5350"
            st.markdown(
                f'<div style="background:#0c1030;border:1px solid #2a2a4a;border-radius:6px;'
                f'padding:7px 14px;margin:3px 0;font-size:12px;'
                f'display:flex;gap:14px;align-items:center;flex-wrap:wrap;">'
                f'<span style="color:#e0e0e0;font-weight:700;min-width:48px;">'
                f'{row.get("ticker","")}</span>'
                f'<span style="color:#666;">{row.get("date","")} {row.get("time","")}</span>'
                f'<span style="color:#888;">{row.get("entry_type","")}</span>'
                f'<span style="color:#888;">{row.get("confirmed","")}</span>'
                f'<span style="color:#555;">News: {row.get("news","")}</span>'
                f'<span style="color:#555;">Window: {row.get("in_window","")}</span>'
                f'<span style="font-weight:700;color:{pc};">{pnl:+.2f}%</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        sa_csv = dfj.to_csv(index=False)
        st.download_button(
            "⬇ Download SA Journal", data=sa_csv,
            file_name="sa_journal.csv", mime="text/csv"
        )
    else:
        st.info("No trades logged yet. Use the expander above to log your first sniper trade.")


tab_chart, tab_scan, tab_journal, tab_tracker, tab_sa = st.tabs(
    ["📈 Main Chart", "🔍 Scanner", "📖 Journal", "🧠 Tracker", "⚡ Small Account"]
)

# ── Scanner tab ────────────────────────────────────────────────────────────────
with tab_scan:
    # ── Run scanner if button clicked ──────────────────────────────────────────
    if scan_button:
        if not api_key or not secret_key:
            st.error("Enter your Alpaca credentials in the sidebar first.")
        else:
            watchlist = [t.strip().upper() for t in watchlist_raw.split(",") if t.strip()]
            if not watchlist:
                st.warning("Watchlist is empty — add some tickers and try again.")
            else:
                with st.spinner(f"Scanning {len(watchlist)} tickers for pre-market gaps…"):
                    try:
                        results = run_gap_scanner(
                            api_key, secret_key, watchlist, date.today(), feed=scan_feed)
                        st.session_state.scanner_results = results
                        st.session_state.scanner_last_run = datetime.now(EASTERN)
                        if not results:
                            st.warning(
                                "Scan ran but returned no results. "
                                "Possible reasons: all tickers outside $1–$50 range, "
                                "no gap data available (market closed / weekend), "
                                "or IEX feed selected (no pre-market data). "
                                "Try switching to SIP feed or checking your watchlist."
                            )
                    except Exception as e:
                        st.error(f"Scanner error: {e}")

    # ── Display results ────────────────────────────────────────────────────────
    results = st.session_state.scanner_results
    last_run = st.session_state.scanner_last_run

    if last_run:
        _pm_ok = results[0].get("pm_data_available", True) if results else True
        _sort_by = "Pre-Market RVOL" if _pm_ok else "Gap %"
        st.caption(f"Last scan: {last_run.strftime('%H:%M:%S')} EST  ·  "
                   f"tickers $1–$50 · sorted by {_sort_by}")
        if not _pm_ok:
            st.info("📊 **Gap-Only Mode** — Pre-market volume unavailable on free IEX tier. "
                    "Results are sorted by largest gap %. PM Vol / RVOL columns will be blank. "
                    "Upgrade to Alpaca SIP subscription to unlock pre-market RVOL.")

    if not results:
        st.info("👈 Click **🔍 Scan Gap Plays** in the sidebar to populate this panel.\n\n"
                "The scanner checks every ticker in your watchlist, filters to the $1–$50 "
                "price range, and ranks them by gap % (free IEX tier) or pre-market RVOL (SIP).")
    else:
        _gap_colors = {
            "up":   ("#4caf50", "#1b5e20"),   # (text, bg-tint)
            "down": ("#ef5350", "#4e1111"),
            "flat": ("#aaaaaa", "#1a1a2e"),
        }
        _rvol_color = lambda r: (
            "#FFD700" if r is not None and r > 5.5 else
            "#FF6B35" if r is not None and r > 4.0 else
            "#FF9500" if r is not None and r > 3.0 else
            "#26a69a" if r is not None and r >= 1.2 else
            "#ef5350"
        )

        for row in results:
            sym   = row["ticker"]
            price = row["price"]
            gap   = row["gap_pct"]
            rvol  = row["pm_rvol"]
            pm_v  = row["pm_vol"]
            avg_v = row["avg_pm_vol"]

            gap_dir    = "up" if gap > 0.2 else "down" if gap < -0.2 else "flat"
            gap_txt    = f"+{gap:.2f}%" if gap >= 0 else f"{gap:.2f}%"
            gap_clr, gap_bg = _gap_colors[gap_dir]
            rc         = _rvol_color(rvol)
            rvol_str   = f"{rvol:.1f}×" if rvol is not None else "N/A"
            pm_str     = f"{pm_v:,}"
            avg_str    = f"{avg_v:,.0f}" if avg_v else "—"

            st.markdown(
                f'<div style="background:#12122299;border:1px solid #2a2a4a;'
                f'border-radius:10px;padding:16px 20px;margin:8px 0;">'
                f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">'
                f'<div>'
                f'<div style="font-size:26px;font-weight:900;color:#e0e0e0;letter-spacing:1px;">{sym}</div>'
                f'<div style="font-size:13px;color:#888;">${price:.2f}</div>'
                f'</div>'
                f'<div style="text-align:center;">'
                f'<div style="font-size:10px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">Gap</div>'
                f'<div style="font-size:22px;font-weight:800;color:{gap_clr};">{gap_txt}</div>'
                f'</div>'
                f'<div style="text-align:center;">'
                f'<div style="font-size:10px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;">PM RVOL</div>'
                f'<div style="font-size:22px;font-weight:800;color:{rc};">{rvol_str}</div>'
                f'<div style="font-size:11px;color:#555;">{pm_str} vs avg {avg_str}</div>'
                f'</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

            # Clickable button — loads Volume Profile for this ticker
            if st.button(f"📊 Load {sym} Volume Profile", key=f"load_{sym}",
                         use_container_width=False):
                st.session_state["_load_ticker"] = sym
                st.session_state["_last_scan_load"] = sym
                st.session_state.auto_run = True
                st.rerun()

        _last = st.session_state.get("_last_scan_load", "")
        if _last:
            st.success(f"✅ **{_last}** loaded — click the **📈 Main Chart** tab to view the Volume Profile.")
        else:
            st.caption("Click a **Load** button to populate the ticker and switch to Main Chart.")

# ── Volume Profile tab: auto_run + all historical/live content ─────────────────
# Consume the auto-run flag set by scanner ticker buttons (runs before tab renders)
auto_trigger = st.session_state.get("auto_run", False)
if auto_trigger:
    st.session_state.auto_run = False

with tab_chart:
    # ── Historical mode ────────────────────────────────────────────────────────
    if mode == "📅 Historical":
        if run_button or auto_trigger:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials in the sidebar.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            elif selected_date.weekday() >= 5:
                st.error("Selected date is a weekend. Pick a weekday (Mon–Fri).")
            else:
                # Fresh analysis — reset alert state so alerts fire for this data
                st.session_state.tcs_fired_high = False
                st.session_state.tcs_was_high = False
                with st.spinner(f"Fetching 1-min bars for **{ticker}** on {selected_date} ({data_feed.upper()})..."):
                    try:
                        df = fetch_bars(api_key, secret_key, ticker, selected_date, feed=data_feed)
                        if df.empty:
                            now_et = datetime.now(EASTERN)
                            pre_mkt = (selected_date >= now_et.date() and
                                       now_et <= EASTERN.localize(datetime(selected_date.year, selected_date.month, selected_date.day, 9, 30)))
                            if pre_mkt:
                                st.warning("Market hasn't opened yet (9:30 AM ET). Come back once trading starts and bars are available.")
                            elif data_feed == "sip":
                                st.warning(f"No data for **{ticker}** on {selected_date} via SIP. Try switching to IEX, or confirm the date was a trading day.")
                            else:
                                st.warning(f"No data for **{ticker}** on {selected_date} via IEX. Small-caps may be absent on IEX — try SIP.")
                        else:
                            st.success(f"Loaded **{len(df)}** 1-min bars via {data_feed.upper()}.")
                            # ── Pre-fetch RVOL baseline (5-day avg daily volume) ──────
                            try:
                                avg_vol = fetch_avg_daily_volume(
                                    api_key, secret_key, ticker, selected_date)
                            except Exception:
                                avg_vol = None
                            st.session_state.rvol_avg_vol = avg_vol

                            # ── Time-segmented RVOL intraday curve ───────────────────
                            try:
                                curve = build_rvol_intraday_curve(
                                    api_key, secret_key, ticker, selected_date,
                                    lookback_days=50, feed=data_feed)
                            except Exception:
                                curve = None
                            st.session_state.rvol_intraday_curve = curve

                            # ── Sector ETF % change for that date ────────────────────
                            try:
                                etf_chg = fetch_etf_pct_change(
                                    api_key, secret_key, sector_etf, selected_date, feed=data_feed)
                            except Exception:
                                etf_chg = 0.0
                            sector_bonus = 10.0 if etf_chg > 1.0 else 0.0
                            st.session_state.sector_pct_chg = etf_chg

                            render_analysis(df, num_bins, ticker,
                                            f"{ticker} — Volume Profile | {selected_date.strftime('%B %d, %Y')}",
                                            avg_daily_vol=avg_vol,
                                            sector_bonus=sector_bonus,
                                            sector_etf=sector_etf,
                                            intraday_curve=curve,
                                            is_live=False)
                            render_log_entry_ui()
                    except Exception as e:
                        err = str(e)
                        if "forbidden" in err.lower() or "403" in err or "unauthorized" in err.lower():
                            st.error("Authentication failed — check your API Key and Secret Key.")
                        elif "subscription" in err.lower() or "not entitled" in err.lower() or "422" in err:
                            if data_feed == "sip":
                                st.warning("SIP feed requires a paid Alpaca subscription. Retrying with IEX…")
                                try:
                                    df2 = fetch_bars(api_key, secret_key, ticker, selected_date, feed="iex")
                                    if df2.empty:
                                        st.error(f"No data for **{ticker}** on {selected_date} via IEX either. Confirm the date was a trading day and the ticker is valid.")
                                    else:
                                        st.success(f"Loaded **{len(df2)}** 1-min bars via IEX (auto-switched from SIP).")
                                        render_analysis(df2, num_bins, ticker,
                                                        f"{ticker} — Volume Profile | {selected_date.strftime('%B %d, %Y')} (IEX)",
                                                        avg_daily_vol=None, sector_bonus=0.0,
                                                        sector_etf=sector_etf, intraday_curve=None,
                                                        is_live=False)
                                        render_log_entry_ui()
                                except Exception as e2:
                                    st.error(f"IEX fallback also failed: {e2}")
                            else:
                                st.error("Not subscribed to IEX feed — check your Alpaca account.")
                        else:
                            st.error(f"Error: {err}")
        else:
            # If a previous analysis exists (e.g. user clicked LOG ENTRY), show
            # the log panel instead of resetting to the welcome screen.
            if st.session_state.get("last_analysis_state"):
                render_log_entry_ui()
            else:
                st.info("👈 Enter credentials and settings in the sidebar, then click **Fetch & Analyze**.")
                st.markdown("### How it works")
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown("**📊 Volume Profile**")
                    st.markdown("Bins the day's 1-min bars to show where volume concentrated.")
                with c2:
                    st.markdown("**🎯 Point of Control**")
                    st.markdown("Gold line — the price level with the highest volume. Price gravitates here.")
                with c3:
                    st.markdown("**📐 Initial Balance**")
                    st.markdown("9:30–10:30 EST High/Low — the key reference range for day structure.")

    # ── Replay mode ────────────────────────────────────────────────────────────
    elif mode == "🎬 Replay":
        # ── Load full-day bars on demand ──────────────────────────────────────
        if replay_load:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials in the sidebar.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            elif selected_date.weekday() >= 5:
                st.error("Selected date is a weekend. Pick a weekday.")
            else:
                with st.spinner(f"Loading full day for **{ticker}** on {selected_date} ({data_feed.upper()})..."):
                    try:
                        _rdf = fetch_bars(api_key, secret_key, ticker, selected_date, feed=data_feed)
                        if _rdf.empty:
                            st.error("No bars returned. Check the ticker/date/feed.")
                        else:
                            st.session_state.replay_bars    = _rdf
                            st.session_state.replay_bar_idx = 0
                            st.session_state.replay_playing = False
                            st.session_state.replay_ticker  = ticker
                            st.session_state.replay_date    = selected_date
                            # Pre-fetch baselines for the replay session
                            try:
                                st.session_state.replay_avg_vol = fetch_avg_daily_volume(
                                    api_key, secret_key, ticker, selected_date)
                            except Exception:
                                st.session_state.replay_avg_vol = None
                            try:
                                st.session_state.replay_intraday_curve = build_rvol_intraday_curve(
                                    api_key, secret_key, ticker, selected_date,
                                    lookback_days=50, feed=data_feed)
                            except Exception:
                                st.session_state.replay_intraday_curve = None
                            try:
                                _etf = fetch_etf_pct_change(
                                    api_key, secret_key, sector_etf, selected_date, feed=data_feed)
                                st.session_state.replay_sector_bonus = 10.0 if _etf > 1.0 else 0.0
                            except Exception:
                                st.session_state.replay_sector_bonus = 0.0
                            # Reset Brain state for fresh replay
                            for _bk in ("brain_ib_high", "brain_ib_low", "brain_ib_set",
                                        "brain_high_touched", "brain_low_touched", "brain_predicted"):
                                st.session_state[_bk] = _DEFAULTS[_bk]
                            st.success(f"✅ Loaded {len(_rdf)} bars — use the slider and controls in the sidebar to replay.")
                            st.rerun()
                    except Exception as _e:
                        st.error(f"Load error: {_e}")

        # ── Run analysis for current replay bar ───────────────────────────────
        if st.session_state.replay_bars is not None:
            _rdf    = st.session_state.replay_bars
            _ridx   = st.session_state.replay_bar_idx
            _rtk    = st.session_state.replay_ticker
            _rdate  = st.session_state.replay_date
            _df_now = _rdf.iloc[:_ridx + 1]   # only bars up to current time

            _cur_et  = pd.Timestamp(_rdf.index[_ridx]).tz_convert(EASTERN)
            _total_et = pd.Timestamp(_rdf.index[-1]).tz_convert(EASTERN)

            # Clock banner
            st.markdown(
                f'<div style="background:#0f3460; border:1px solid #5c6bc0; border-radius:8px; '
                f'padding:10px 20px; margin-bottom:10px; display:flex; align-items:center; gap:16px;">'
                f'<span style="font-size:22px; font-family:monospace; color:#90caf9; font-weight:700;">'
                f'🎬 {_cur_et.strftime("%H:%M")} ET</span>'
                f'<span style="font-size:12px; color:#888;">Bar {_ridx + 1} / {len(_rdf)} &nbsp;|&nbsp; '
                f'{_rtk} &nbsp;|&nbsp; {_rdate}</span>'
                f'<span style="font-size:11px; color:#555; margin-left:auto;">'
                f'through {_total_et.strftime("%H:%M")} ET total</span></div>',
                unsafe_allow_html=True,
            )

            if len(_df_now) < 2:
                st.info("Need at least 2 bars for a full analysis — advance the slider forward.")
            else:
                render_analysis(
                    _df_now, num_bins, _rtk,
                    f"🎬 Replay — {_rtk} | {_cur_et.strftime('%H:%M ET')} | {_rdate}",
                    is_ib_live=(_cur_et.time() <= dtime(10, 30)),
                    avg_daily_vol=st.session_state.replay_avg_vol,
                    sector_bonus=st.session_state.replay_sector_bonus,
                    sector_etf=sector_etf,
                    intraday_curve=st.session_state.replay_intraday_curve,
                    is_live=False,
                )
                render_log_entry_ui()
        else:
            st.info("👈 Pick a date and click **📥 Load Day for Replay** in the sidebar.")
            rc1, rc2 = st.columns(2)
            with rc1:
                st.markdown("**How Replay works**")
                st.markdown(
                    "- Fetches the full day's bars once\n"
                    "- Shows only bars up to the selected time\n"
                    "- All analysis (Volume Profile, Structure, TCS, Brain) updates in real-time\n"
                    "- Use the slider to jump to any moment, or ▶ Play to auto-advance"
                )
            with rc2:
                st.markdown("**Uses**")
                st.markdown(
                    "- Review your trade decisions at the exact moment you entered\n"
                    "- See how the IB formed bar by bar\n"
                    "- Practice reading structure before it becomes obvious\n"
                    "- Study how RVOL evolved during the session"
                )

    # ── Live mode ──────────────────────────────────────────────────────────────
    else:
        if start_live:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials first.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            else:
                # Pre-fetch RVOL baseline and sector ETF change before starting stream
                today = date.today()
                with st.spinner("Computing RVOL baseline & sector data…"):
                    try:
                        avg_vol = fetch_avg_daily_volume(api_key, secret_key, ticker, today)
                    except Exception:
                        avg_vol = None
                    st.session_state.rvol_avg_vol = avg_vol

                    try:
                        curve = build_rvol_intraday_curve(
                            api_key, secret_key, ticker, today,
                            lookback_days=50, feed=live_feed)
                    except Exception:
                        curve = None
                    st.session_state.rvol_intraday_curve = curve

                    try:
                        etf_chg = fetch_etf_pct_change(
                            api_key, secret_key, sector_etf, today, feed=live_feed)
                    except Exception:
                        etf_chg = 0.0
                    st.session_state.sector_pct_chg = etf_chg

                start_stream(api_key, secret_key, ticker, live_feed)
                st.rerun()

        if stop_live:
            stop_stream()
            st.rerun()

        if st.session_state.live_error:
            err = st.session_state.live_error
            if "forbidden" in err.lower() or "unauthorized" in err.lower():
                st.error(f"Auth error: {err}. Check your API credentials.")
            elif "subscription" in err.lower() or "entitled" in err.lower():
                st.error("Subscription error: SIP requires an Alpaca data plan. Switch to IEX.")
            else:
                st.error(f"Stream error: {err}")

        if st.session_state.live_active:
            drain_queue()
            df = build_live_df()
            now_est = datetime.now(EASTERN)
            is_ib_live = now_est.time() <= dtime(10, 30)

            if df.empty:
                if now_est.time() < dtime(9, 30):
                    mins = int((datetime.combine(date.today(), dtime(9, 30)) -
                                now_est.replace(tzinfo=None)).total_seconds() // 60)
                    st.info(f"⏳ Market opens in ~{mins} min (9:30 AM EST). WebSocket connected, waiting...")
                elif now_est.time() > dtime(16, 0):
                    st.warning("Market is closed. No new data will arrive until tomorrow's session.")
                else:
                    st.info(f"🔌 Connected. Waiting for first trade on **{st.session_state.live_ticker}**... "
                            f"({now_est.strftime('%H:%M:%S')} EST)")
            else:
                chart_title = (f"🔴 LIVE — {st.session_state.live_ticker} | "
                               f"{now_est.strftime('%H:%M:%S')} EST"
                               + (" | 📐 IB FORMING" if is_ib_live else ""))
                live_sector_bonus = (10.0
                                     if st.session_state.get("sector_pct_chg", 0.0) > 1.0
                                     else 0.0)
                render_analysis(df, num_bins, st.session_state.live_ticker,
                                chart_title, is_ib_live=is_ib_live,
                                avg_daily_vol=st.session_state.get("rvol_avg_vol"),
                                sector_bonus=live_sector_bonus,
                                sector_etf=sector_etf,
                                intraday_curve=st.session_state.get("rvol_intraday_curve"),
                                is_live=True)
                render_log_entry_ui()
        else:
            if not st.session_state.live_error:
                st.info("👈 Enter credentials and ticker, then click **▶ Start Live Stream**.")
                st.markdown("### What Live Mode does")
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.markdown("**🔌 WebSocket Feed**")
                    st.markdown("Subscribes to real-time trades + 1-min bars from Alpaca.")
                with c2:
                    st.markdown("**📐 Dynamic IB**")
                    st.markdown("IB High/Low lines expand in real time from 9:30–10:30 AM EST.")
                with c3:
                    st.markdown("**⚡ Vol Velocity**")
                    st.markdown("Compares recent vol/min to prior bars — early breakout signal.")
                with c4:
                    st.markdown("**🎯 Probabilities**")
                    st.markdown("Every structure type scored continuously as the tape develops.")

# ── Journal tab ───────────────────────────────────────────────────────────────
with tab_journal:
    render_journal_tab()

# ── Tracker tab ───────────────────────────────────────────────────────────────
with tab_tracker:
    render_tracker_tab()

# ── Small Account Challenge tab ────────────────────────────────────────────────
with tab_sa:
    render_sa_tab()

# ── Auto-refresh loop for live mode ───────────────────────────────────────────
if mode == "🔴 Live Stream" and st.session_state.live_active:
    time.sleep(2)
    st.rerun()

# ── Replay auto-advance loop ──────────────────────────────────────────────────
if (mode == "🎬 Replay"
        and st.session_state.replay_playing
        and st.session_state.replay_bars is not None):
    _rdf_all = st.session_state.replay_bars
    _max     = len(_rdf_all) - 1
    _nxt     = min(_max, st.session_state.replay_bar_idx + st.session_state.replay_speed)
    if _nxt >= _max:
        st.session_state.replay_playing = False   # reached end, stop
    st.session_state.replay_bar_idx = _nxt
    time.sleep(0.5)   # ~2 bars/sec at Normal speed
    st.rerun()
