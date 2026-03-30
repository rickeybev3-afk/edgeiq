import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta, time as dtime
import pytz
import threading
import queue
import time
from collections import deque

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
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

EASTERN = pytz.timezone("America/New_York")

# ══════════════════════════════════════════════════════════════════════════════
# CORE MATH
# ══════════════════════════════════════════════════════════════════════════════

def fetch_bars(api_key, secret_key, ticker, trade_date, feed="sip"):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(api_key, secret_key)
    mo = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30))
    mc = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 16, 0))
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                           start=mo, end=mc, feed=feed)
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(EASTERN)
    df = df.sort_index()
    df = df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))]
    return df


def compute_initial_balance(df):
    ib_end = df.index[0].replace(hour=10, minute=30, second=0)
    ib_data = df[df.index <= ib_end]
    if ib_data.empty:
        return None, None
    return float(ib_data["high"].max()), float(ib_data["low"].min())


def compute_volume_profile(df, num_bins):
    price_min = df["low"].min()
    price_max = df["high"].max()
    bins = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    vap = np.zeros(num_bins)
    for _, row in df.iterrows():
        lo, hi, vol = row["low"], row["high"], row["volume"]
        i0 = max(0, int(np.searchsorted(bins, lo, side="left")) - 1)
        i1 = min(num_bins, int(np.searchsorted(bins, hi, side="right")))
        sp = i1 - i0
        if sp > 0:
            vap[i0:i1] += vol / sp
    poc_idx = int(np.argmax(vap))
    return bin_centers, vap, float(bin_centers[poc_idx])


def _find_peaks(smoothed, bin_centers, threshold_pct=0.30):
    """Return indices of local maxima that exceed threshold_pct of the profile max."""
    n = len(smoothed)
    max_v = smoothed.max()
    peaks = []
    for i in range(3, n - 3):
        if (smoothed[i] >= max_v * threshold_pct and
                smoothed[i] > smoothed[i-1] and smoothed[i] > smoothed[i+1] and
                smoothed[i] > smoothed[i-2] and smoothed[i] > smoothed[i+2]):
            # Deduplicate: require at least 3 bins from the previous accepted peak
            if not peaks or (i - peaks[-1]) >= 3:
                peaks.append(i)
    return peaks


def _is_strong_hvn(pk, vap):
    """True if peak qualifies as an HVN by small-cap DD criteria.

    Either:
      • Volume in ±2-bin window around peak > 20 % of total session volume, OR
      • Peak bin volume > 2.5× the average bin volume.
    """
    total_vol = vap.sum()
    if total_vol == 0:
        return False
    avg_bin = total_vol / len(vap)
    window = vap[max(0, pk-2): min(len(vap), pk+3)].sum()
    return (window / total_vol > 0.20) or (vap[pk] > 2.5 * avg_bin)


def _detect_double_distribution(bin_centers, vap, min_bin_sep=15):
    """Return (pk1_idx, pk2_idx, lvn_idx) if a valid Double Distribution is found, else None."""
    smoothed = np.convolve(vap.astype(float), np.ones(5)/5, mode="same")
    peaks = _find_peaks(smoothed, bin_centers, threshold_pct=0.25)
    for j in range(len(peaks) - 1):
        pk1, pk2 = peaks[j], peaks[j+1]
        # Must be at least 15 bins apart
        if (pk2 - pk1) < min_bin_sep:
            continue
        # Both peaks must qualify as strong HVNs
        if not (_is_strong_hvn(pk1, vap) and _is_strong_hvn(pk2, vap)):
            continue
        # Must have a clear LVN valley between them
        vi = int(np.argmin(smoothed[pk1:pk2+1])) + pk1
        if smoothed[vi] < 0.60 * min(smoothed[pk1], smoothed[pk2]):
            return pk1, pk2, vi
    return None


def classify_day_structure(df, bin_centers, vap, ib_high, ib_low, poc_price):
    day_high = float(df["high"].max())
    day_low = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = ib_high - ib_low
    final_price = float(df["close"].iloc[-1])
    if total_range == 0 or ib_range == 0:
        return "⚖️ Normal / Balanced", "#66bb6a", "Insufficient range data."
    poc_pos = (poc_price - day_low) / total_range

    # ── 1. Double Distribution (prioritised — check before single-direction labels) ──
    dd = _detect_double_distribution(bin_centers, vap)
    if dd is not None:
        pk1, pk2, vi = dd
        sep_bins = pk2 - pk1
        sep_price = bin_centers[pk2] - bin_centers[pk1]
        pct1 = vap[max(0,pk1-2):min(len(vap),pk1+3)].sum() / vap.sum() * 100
        pct2 = vap[max(0,pk2-2):min(len(vap),pk2+3)].sum() / vap.sum() * 100
        return ("⚡ Double Distribution", "#00bcd4",
                f"HVNs at ${bin_centers[pk1]:.2f} ({pct1:.0f}% vol) & "
                f"${bin_centers[pk2]:.2f} ({pct2:.0f}% vol) — "
                f"{sep_bins}-bin / ${sep_price:.2f} gap. "
                f"LVN at ${bin_centers[vi]:.2f}. Two distinct auctions.")

    # ── 2. Trend Day ──────────────────────────────────────────────────────────
    if total_range > 2.5 * ib_range:
        bull = final_price > day_low + total_range / 2
        return ("📈 Trend Day", "#ff9800",
                f"{'Bullish' if bull else 'Bearish'} — range ${total_range:.2f} is "
                f"{total_range/ib_range:.1f}× the IB (${ib_range:.2f}). Strong directional conviction.")

    # ── 3. P-Shape ────────────────────────────────────────────────────────────
    if poc_pos >= 0.75 and final_price > ib_high:
        return ("🅟 P-Shape (Short Covering)", "#ce93d8",
                f"POC ${poc_price:.2f} in top {100*(1-poc_pos):.0f}% of range, "
                f"close ${final_price:.2f} > IB High ${ib_high:.2f}. Shorts covering into strength.")

    # ── 4. b-Shape ────────────────────────────────────────────────────────────
    if poc_pos <= 0.25 and final_price < ib_low:
        return ("🅑 b-Shape (Long Liquidation)", "#ef5350",
                f"POC ${poc_price:.2f} in bottom {100*poc_pos:.0f}% of range, "
                f"close ${final_price:.2f} < IB Low ${ib_low:.2f}. Longs liquidating into weakness.")

    # ── 5. Normal / Balanced ──────────────────────────────────────────────────
    pct = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
    return ("⚖️ Normal / Balanced", "#66bb6a",
            f"Price inside IB for {pct:.0f}% of session — balanced, rotational day.")


def compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price):
    day_high = float(df["high"].max())
    day_low = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = ib_high - ib_low
    final_price = float(df["close"].iloc[-1])
    if total_range == 0 or ib_range == 0:
        return {"Trend Day": 20.0, "P-Shape": 20.0, "b-Shape": 20.0, "Dbl Dist": 20.0, "Normal": 20.0}

    rr = total_range / ib_range
    poc_pos = (poc_price - day_low) / total_range
    pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean())

    td = 5.0 + max(0.0, (rr - 1.0) * 28.0)
    td = min(td, 90.0)

    ps = 5.0 + max(0.0, (poc_pos - 0.50) * 65.0)
    if final_price > ib_high:
        ps += 18.0
    ps = min(ps, 80.0)

    bs = 5.0 + max(0.0, (0.50 - poc_pos) * 65.0)
    if final_price < ib_low:
        bs += 18.0
    bs = min(bs, 80.0)

    # Use the strict DD detector — bump score significantly if it fires
    has_dd = _detect_double_distribution(bin_centers, vap) is not None
    dd = 55.0 if has_dd else 5.0

    nm = 8.0 + pct_inside * 50.0

    scores = {"Trend Day": td, "P-Shape": ps, "b-Shape": bs, "Dbl Dist": dd, "Normal": nm}
    total = sum(scores.values())
    return {k: round(v / total * 100, 1) for k, v in scores.items()}


def compute_tcs(df, ib_high, ib_low, poc_price):
    """Trend Confidence Score (0–100).

    Three equally-weighted factors:
      • Range Factor   (40 pts) — day range vs IB range
      • Velocity Factor (30 pts) — current vol/min vs session avg vol/min
      • Structure Factor (30 pts) — price > 1 ATR from POC and trending away
    """
    tcs = 0.0

    day_high = float(df["high"].max())
    day_low = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = (ib_high - ib_low) if (ib_high and ib_low) else 0.0
    final_price = float(df["close"].iloc[-1])

    # ── Range Factor (40 pts) ─────────────────────────────────────────────────
    if ib_range > 0:
        rr = total_range / ib_range
        if rr >= 2.5:
            tcs += 40.0
        elif rr > 1.1:
            tcs += 40.0 * (rr - 1.1) / (2.5 - 1.1)

    # ── Velocity Factor (30 pts) ──────────────────────────────────────────────
    if len(df) >= 6:
        w = min(3, len(df) // 2)
        current_vel = float(df["volume"].iloc[-w:].mean())
        avg_vel = float(df["volume"].mean())
        if avg_vel > 0:
            vr = current_vel / avg_vel
            if vr >= 2.0:
                tcs += 30.0
            elif vr > 1.0:
                tcs += 30.0 * (vr - 1.0) / (2.0 - 1.0)

    # ── Structure Factor (30 pts) ─────────────────────────────────────────────
    if len(df) >= 3:
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr = float(tr.rolling(window=min(14, len(df))).mean().iloc[-1])

        if atr > 0 and abs(final_price - poc_price) > atr:
            # "Moving" = last 3 closes trending further from POC
            if len(df) >= 4:
                poc_side = 1 if final_price > poc_price else -1
                move = float(df["close"].iloc[-1]) - float(df["close"].iloc[-4])
                if move * poc_side > 0:
                    tcs += 30.0          # trending away — full credit
                else:
                    tcs += 15.0          # beyond ATR but stalling
            else:
                tcs += 20.0

    return round(min(100.0, tcs), 1)


def compute_volume_velocity(df):
    if len(df) < 4:
        return None, None, None
    w = min(3, len(df) // 2)
    recent = float(df["volume"].iloc[-w:].mean())
    if len(df) < 2 * w:
        return recent, None, None
    prev = float(df["volume"].iloc[-2*w:-w].mean())
    if prev == 0:
        return recent, None, None
    chg = (recent - prev) / prev * 100
    return recent, abs(chg), ("↑" if chg >= 0 else "↓")


# ══════════════════════════════════════════════════════════════════════════════
# LIVE STREAM
# ══════════════════════════════════════════════════════════════════════════════

def _stream_worker(api_key, secret_key, ticker, feed_str, data_queue, stop_event):
    import asyncio
    from alpaca.data.live import StockDataStream
    from alpaca.data.enums import DataFeed

    feed_enum = DataFeed.SIP if feed_str == "sip" else DataFeed.IEX
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stream = StockDataStream(api_key, secret_key, feed=feed_enum)

    async def on_trade(trade):
        try:
            data_queue.put_nowait({"t": "trade", "p": float(trade.price),
                                   "s": float(trade.size), "ts": trade.timestamp})
        except Exception:
            pass

    async def on_bar(bar):
        try:
            data_queue.put_nowait({"t": "bar", "o": float(bar.open), "h": float(bar.high),
                                   "l": float(bar.low), "c": float(bar.close),
                                   "v": float(bar.volume), "ts": bar.timestamp})
        except Exception:
            pass

    stream.subscribe_trades(on_trade, ticker)
    stream.subscribe_bars(on_bar, ticker)

    async def run_until_stopped():
        task = asyncio.create_task(stream.run())
        while not stop_event.is_set():
            await asyncio.sleep(0.3)
        stream.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except Exception:
            task.cancel()

    try:
        loop.run_until_complete(run_until_stopped())
    except Exception as e:
        try:
            data_queue.put_nowait({"t": "error", "msg": str(e)})
        except Exception:
            pass
    finally:
        try:
            loop.close()
        except Exception:
            pass


def start_stream(api_key, secret_key, ticker, feed_str):
    q = queue.Queue(maxsize=10000)
    ev = threading.Event()
    t = threading.Thread(target=_stream_worker,
                         args=(api_key, secret_key, ticker, feed_str, q, ev),
                         daemon=True)
    t.start()
    st.session_state.live_queue = q
    st.session_state.live_stop_event = ev
    st.session_state.live_thread = t
    st.session_state.live_active = True
    st.session_state.live_bars = []
    st.session_state.live_current_bar = None
    st.session_state.live_trades = deque(maxlen=3000)
    st.session_state.live_ticker = ticker
    st.session_state.live_error = None


def stop_stream():
    if st.session_state.live_stop_event:
        st.session_state.live_stop_event.set()
    st.session_state.live_active = False
    st.session_state.live_queue = None
    st.session_state.live_stop_event = None
    st.session_state.live_thread = None


def drain_queue():
    q = st.session_state.live_queue
    if q is None:
        return
    cur = st.session_state.live_current_bar or {}
    processed = 0
    while processed < 1000:
        try:
            item = q.get_nowait()
            processed += 1
        except queue.Empty:
            break
        t = item.get("t")
        if t == "error":
            st.session_state.live_error = item.get("msg", "Unknown error")
            st.session_state.live_active = False
        elif t == "bar":
            st.session_state.live_bars.append(
                {"open": item["o"], "high": item["h"], "low": item["l"],
                 "close": item["c"], "volume": item["v"], "timestamp": item["ts"]}
            )
            cur = {}
        elif t == "trade":
            p, s, ts = item["p"], item["s"], item["ts"]
            st.session_state.live_trades.append({"price": p, "size": s, "ts": ts})
            if not cur:
                cur = {"open": p, "high": p, "low": p, "close": p, "volume": s, "timestamp": ts}
            else:
                cur["high"] = max(cur["high"], p)
                cur["low"] = min(cur["low"], p)
                cur["close"] = p
                cur["volume"] = cur.get("volume", 0) + s
                cur["timestamp"] = ts
    st.session_state.live_current_bar = cur if cur else None


def build_live_df():
    rows = list(st.session_state.live_bars)
    if st.session_state.live_current_bar:
        rows.append(st.session_state.live_current_bar)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["timestamp"])
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(EASTERN)
    df = df.drop(columns=["timestamp"], errors="ignore")
    needed = ["open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in needed):
        return pd.DataFrame()
    df = df[needed].sort_index()
    df = df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))]
    return df


# ══════════════════════════════════════════════════════════════════════════════
# CHART & RENDER
# ══════════════════════════════════════════════════════════════════════════════

def build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, title):
    fig = make_subplots(rows=1, cols=2, column_widths=[0.75, 0.25],
                        shared_yaxes=True, horizontal_spacing=0.01)
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
    ), row=1, col=1)

    x0, x1 = df.index[0], df.index[-1]
    if ib_high is not None and ib_low is not None:
        fig.add_trace(go.Scatter(x=[x0, x1], y=[ib_high, ib_high], mode="lines",
            name=f"IB High ({ib_high:.2f})",
            line=dict(color="#00e676", width=1.8, dash="dash")), row=1, col=1)
        fig.add_trace(go.Scatter(x=[x0, x1], y=[ib_low, ib_low], mode="lines",
            name=f"IB Low ({ib_low:.2f})",
            line=dict(color="#ff5252", width=1.8, dash="dash")), row=1, col=1)

    fig.add_trace(go.Scatter(x=[x0, x1], y=[poc_price, poc_price], mode="lines",
        name=f"POC ({poc_price:.2f})", line=dict(color="gold", width=2.5)), row=1, col=1)

    bw = float(bin_centers[1] - bin_centers[0]) if len(bin_centers) > 1 else 0
    colors = ["gold" if abs(p - poc_price) < bw * 0.5 else "#5c6bc0" for p in bin_centers]
    fig.add_trace(go.Bar(x=vap, y=bin_centers, orientation="h",
        name="Volume Profile", marker_color=colors, opacity=0.85), row=1, col=2)

    fig.update_layout(
        title=dict(text=title, font=dict(size=17, color="white")),
        paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e", font=dict(color="#e0e0e0"),
        height=660,
        xaxis=dict(rangeslider=dict(visible=False), gridcolor="#2a2a4a",
                   showgrid=True, type="category"),
        yaxis=dict(gridcolor="#2a2a4a", showgrid=True, tickformat=".2f"),
        xaxis2=dict(gridcolor="#2a2a4a", showgrid=True, title="Volume"),
        legend=dict(bgcolor="#0f3460", bordercolor="#5c6bc0", borderwidth=1,
                    font=dict(color="white"), x=0.01, y=0.99),
        margin=dict(l=10, r=10, t=55, b=40),
    )
    fig.update_xaxes(nticks=20, tickangle=-45, row=1, col=1)
    return fig


def render_structure_banner(label, color, detail, probs, tcs):
    top3 = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:3]
    prob_pills = "".join(
        f'<span style="display:inline-block; background:{color}33; border:1px solid {color}66; '
        f'border-radius:4px; padding:2px 8px; margin:2px 4px; font-size:13px; color:#eee;">'
        f'<b>{n}</b> {p}%</span>'
        for n, p in top3
    )

    # TCS gauge colours
    if tcs >= 70:
        gauge_color = "#4caf50"
        badge = ('<span style="background:#4caf5033; border:1px solid #4caf50; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#4caf50; '
                 'text-transform:uppercase; letter-spacing:1px;">🔥 HIGH CONVICTION</span>')
    elif tcs <= 30:
        gauge_color = "#ef5350"
        badge = ('<span style="background:#ef535033; border:1px solid #ef5350; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#ef5350; '
                 'text-transform:uppercase; letter-spacing:1px;">⚠ CHOP RISK</span>')
    else:
        gauge_color = "#ffa726"
        badge = ""

    tcs_bar = f"""
    <div style="margin-top:10px; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px; white-space:nowrap;">
            Trend Confidence
        </span>
        <div style="flex:1; min-width:120px; max-width:220px; background:#2a2a4a;
                    border-radius:6px; height:10px; overflow:hidden;">
            <div style="width:{tcs}%; background:linear-gradient(90deg,{gauge_color}99,{gauge_color});
                        height:100%; border-radius:6px; transition:width 0.4s;"></div>
        </div>
        <span style="font-size:18px; font-weight:800; color:{gauge_color}; min-width:44px;">{tcs:.0f}%</span>
        {badge}
    </div>
    """

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{color}22,{color}0a);
                border-left:5px solid {color}; border-radius:8px;
                padding:14px 22px; margin:10px 0 4px 0;">
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


def render_analysis(df, num_bins, ticker, chart_title, is_ib_live=False):
    ib_high, ib_low = compute_initial_balance(df)
    bin_centers, vap, poc_price = compute_volume_profile(df, num_bins)
    label, color, detail = classify_day_structure(df, bin_centers, vap, ib_high, ib_low, poc_price)
    probs = compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price)
    tcs = compute_tcs(df, ib_high, ib_low, poc_price)

    day_high = float(df["high"].max())
    day_low = float(df["low"].min())
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

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Bars", len(df))
    col2.metric("IB High", f"${ib_high:.2f}" if ib_high else "—")
    col3.metric("IB Low", f"${ib_low:.2f}" if ib_low else "—")
    col4.metric("IB Range", f"${ib_range:.2f}")
    col5.metric("Day Range", f"${day_high - day_low:.2f}")
    col6.metric("POC", f"${poc_price:.2f}")

    render_velocity_widget(df)
    render_structure_banner(label, color, detail, probs, tcs)

    fig = build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, chart_title)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("📋 Raw Bar Data"):
        disp = df[["open", "high", "low", "close", "volume"]].copy()
        disp.index = disp.index.strftime("%H:%M")
        disp.columns = ["Open", "High", "Low", "Close", "Volume"]
        st.dataframe(disp, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("🔑 Alpaca Credentials")
    api_key = st.text_input("API Key", type="password", placeholder="Alpaca API Key")
    secret_key = st.text_input("Secret Key", type="password", placeholder="Alpaca Secret Key")

    st.markdown("---")
    mode = st.radio("Mode", ["📅 Historical", "🔴 Live Stream"], index=0)

    st.markdown("---")
    st.header("📈 Settings")
    ticker = st.text_input("Ticker Symbol", value="AAPL", placeholder="e.g. AAPL, GME").upper().strip()
    num_bins = st.slider("Volume Profile Bins", min_value=20, max_value=200, value=100, step=10)

    run_button = start_live = stop_live = False
    selected_date = date.today()
    data_feed = "sip"

    if mode == "📅 Historical":
        today = date.today()
        def_d = today - timedelta(days=1)
        if def_d.weekday() == 6:
            def_d -= timedelta(days=2)
        elif def_d.weekday() == 5:
            def_d -= timedelta(days=1)
        selected_date = st.date_input("Trading Date", value=def_d, max_value=today,
                                       help="Pick a weekday (Mon–Fri)")
        data_feed = st.selectbox("Data Feed", ["sip", "iex"], index=0,
                                  help="SIP = full tape. IEX = IEX exchange only.")
        run_button = st.button("🚀 Fetch & Analyze", use_container_width=True, type="primary")
    else:
        live_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX works on all accounts. SIP needs a subscription.")
        if not st.session_state.live_active:
            start_live = st.button("▶ Start Live Stream", use_container_width=True, type="primary")
        else:
            stop_live = st.button("⏹ Stop", use_container_width=True)
            st.success(f"🔴 Live: **{st.session_state.live_ticker}**")

    st.markdown("---")
    st.caption("SIP = full national tape (small-caps need this). IEX = IEX exchange only.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN AREA
# ══════════════════════════════════════════════════════════════════════════════

if mode == "🔴 Live Stream" and st.session_state.live_active:
    st.title("📊 Volume Profile Dashboard")
    st.markdown(f"🔴 **Live tape** — `{st.session_state.live_ticker}` — chart refreshes every 2 s")
else:
    st.title("📊 Volume Profile Dashboard — Small Cap Stocks")
    st.markdown("Visualize Volume Profile structures with Point of Control (POC) and Initial Balance (IB).")

# ── Historical mode ────────────────────────────────────────────────────────────
if mode == "📅 Historical":
    if run_button:
        if not api_key or not secret_key:
            st.error("Enter your Alpaca credentials in the sidebar.")
        elif not ticker:
            st.error("Enter a ticker symbol.")
        elif selected_date.weekday() >= 5:
            st.error("Selected date is a weekend. Pick a weekday (Mon–Fri).")
        else:
            with st.spinner(f"Fetching 1-min bars for **{ticker}** on {selected_date} ({data_feed.upper()})..."):
                try:
                    df = fetch_bars(api_key, secret_key, ticker, selected_date, feed=data_feed)
                    if df.empty:
                        if data_feed == "sip":
                            st.warning(f"No data for **{ticker}** on {selected_date} via SIP. Try IEX, or confirm the date was a trading day.")
                        else:
                            st.warning(f"No data for **{ticker}** on {selected_date} via IEX. Small-caps are often absent on IEX — try SIP.")
                    else:
                        st.success(f"Loaded **{len(df)}** 1-min bars via {data_feed.upper()}.")
                        render_analysis(df, num_bins, ticker,
                                        f"{ticker} — Volume Profile | {selected_date.strftime('%B %d, %Y')}")
                except Exception as e:
                    err = str(e)
                    if "forbidden" in err.lower() or "403" in err or "unauthorized" in err.lower():
                        st.error("Authentication failed — check your API Key and Secret Key.")
                    elif "subscription" in err.lower() or "not entitled" in err.lower() or "422" in err:
                        st.error(f"Not subscribed to {data_feed.upper()} feed. Switch to IEX or upgrade your Alpaca plan.")
                    else:
                        st.error(f"Error: {err}")
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

# ── Live mode ──────────────────────────────────────────────────────────────────
else:
    if start_live:
        if not api_key or not secret_key:
            st.error("Enter your Alpaca credentials first.")
        elif not ticker:
            st.error("Enter a ticker symbol.")
        else:
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
            render_analysis(df, num_bins, st.session_state.live_ticker,
                            chart_title, is_ib_live=is_ib_live)
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

# ── Auto-refresh loop for live mode ───────────────────────────────────────────
if mode == "🔴 Live Stream" and st.session_state.live_active:
    time.sleep(2)
    st.rerun()
