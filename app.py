import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta
import pytz

st.set_page_config(
    page_title="Volume Profile Dashboard",
    page_icon="📊",
    layout="wide"
)

st.title("📊 Volume Profile Dashboard — Small Cap Stocks")
st.markdown("Visualize Volume Profile structures with Point of Control (POC) and Initial Balance (IB) levels.")

with st.sidebar:
    st.header("🔑 Alpaca Credentials")
    api_key = st.text_input("API Key", type="password", placeholder="Enter your Alpaca API Key")
    secret_key = st.text_input("Secret Key", type="password", placeholder="Enter your Alpaca Secret Key")

    st.markdown("---")
    st.header("📈 Chart Settings")
    ticker = st.text_input("Ticker Symbol", value="AAPL", placeholder="e.g. AAPL, TSLA, GME").upper().strip()

    today = date.today()
    default_date = today - timedelta(days=1)
    if default_date.weekday() == 6:
        default_date -= timedelta(days=2)
    elif default_date.weekday() == 5:
        default_date -= timedelta(days=1)

    selected_date = st.date_input(
        "Trading Date",
        value=default_date,
        max_value=today,
        help="Select a trading day (Mon–Fri)"
    )

    num_bins = st.slider("Volume Profile Bins", min_value=20, max_value=200, value=100, step=10)

    run_button = st.button("🚀 Fetch & Analyze", use_container_width=True, type="primary")

    st.markdown("---")
    st.markdown(
        "**Tip:** Use paper trading keys for testing. "
        "Make sure the selected date is a valid trading day."
    )

def fetch_bars(api_key: str, secret_key: str, ticker: str, trade_date: date) -> pd.DataFrame:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key, secret_key)

    eastern = pytz.timezone("America/New_York")
    market_open = eastern.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30, 0))
    market_close = eastern.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 16, 0, 0))

    request_params = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Minute,
        start=market_open,
        end=market_close,
        feed="iex"
    )

    bars = client.get_stock_bars(request_params)
    df = bars.df

    if df.empty:
        return pd.DataFrame()

    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")

    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("America/New_York")

    df = df.sort_index()
    df = df[(df.index.time >= datetime.strptime("09:30", "%H:%M").time()) &
            (df.index.time <= datetime.strptime("16:00", "%H:%M").time())]

    return df

def compute_initial_balance(df: pd.DataFrame):
    ib_end = df.index[0].replace(hour=10, minute=30, second=0)
    ib_data = df[df.index <= ib_end]
    if ib_data.empty:
        return None, None
    ib_high = ib_data["high"].max()
    ib_low = ib_data["low"].min()
    return ib_high, ib_low

def compute_volume_profile(df: pd.DataFrame, num_bins: int):
    price_min = df["low"].min()
    price_max = df["high"].max()
    bins = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    volume_at_price = np.zeros(num_bins)

    for _, row in df.iterrows():
        lo, hi, vol = row["low"], row["high"], row["volume"]
        idx_lo = np.searchsorted(bins, lo, side="left")
        idx_hi = np.searchsorted(bins, hi, side="right")
        idx_lo = max(0, idx_lo - 1)
        idx_hi = min(num_bins, idx_hi)
        spread = idx_hi - idx_lo
        if spread > 0:
            volume_at_price[idx_lo:idx_hi] += vol / spread

    poc_idx = np.argmax(volume_at_price)
    poc_price = bin_centers[poc_idx]

    return bin_centers, volume_at_price, poc_price

def build_chart(df: pd.DataFrame, ib_high: float, ib_low: float,
                bin_centers: np.ndarray, volume_at_price: np.ndarray,
                poc_price: float, ticker: str, trade_date: date) -> go.Figure:

    fig = make_subplots(
        rows=1, cols=2,
        column_widths=[0.75, 0.25],
        shared_yaxes=True,
        horizontal_spacing=0.01
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="Price",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",
            decreasing_fillcolor="#ef5350",
        ),
        row=1, col=1
    )

    x_start = df.index[0]
    x_end = df.index[-1]

    fig.add_trace(
        go.Scatter(
            x=[x_start, x_end],
            y=[ib_high, ib_high],
            mode="lines",
            name=f"IB High ({ib_high:.2f})",
            line=dict(color="#00e676", width=1.5, dash="dash"),
            showlegend=True
        ),
        row=1, col=1
    )

    fig.add_trace(
        go.Scatter(
            x=[x_start, x_end],
            y=[ib_low, ib_low],
            mode="lines",
            name=f"IB Low ({ib_low:.2f})",
            line=dict(color="#ff5252", width=1.5, dash="dash"),
            showlegend=True
        ),
        row=1, col=1
    )

    fig.add_trace(
        go.Scatter(
            x=[x_start, x_end],
            y=[poc_price, poc_price],
            mode="lines",
            name=f"POC ({poc_price:.2f})",
            line=dict(color="gold", width=2.5),
            showlegend=True
        ),
        row=1, col=1
    )

    colors = ["gold" if abs(p - poc_price) < (bin_centers[1] - bin_centers[0]) * 0.5
              else "#5c6bc0" for p in bin_centers]

    fig.add_trace(
        go.Bar(
            x=volume_at_price,
            y=bin_centers,
            orientation="h",
            name="Volume Profile",
            marker_color=colors,
            showlegend=True,
            opacity=0.85
        ),
        row=1, col=2
    )

    fig.update_layout(
        title=dict(
            text=f"{ticker} — Volume Profile | {trade_date.strftime('%B %d, %Y')}",
            font=dict(size=18, color="white")
        ),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        height=680,
        xaxis=dict(
            rangeslider=dict(visible=False),
            gridcolor="#2a2a4a",
            showgrid=True,
            type="category"
        ),
        yaxis=dict(
            gridcolor="#2a2a4a",
            showgrid=True,
            tickformat=".2f"
        ),
        xaxis2=dict(
            gridcolor="#2a2a4a",
            showgrid=True,
            title="Volume"
        ),
        legend=dict(
            bgcolor="#0f3460",
            bordercolor="#5c6bc0",
            borderwidth=1,
            font=dict(color="white"),
            x=0.01,
            y=0.99
        ),
        margin=dict(l=10, r=10, t=60, b=40)
    )

    fig.update_xaxes(nticks=20, tickangle=-45, row=1, col=1)

    return fig


if run_button:
    if not api_key or not secret_key:
        st.error("Please enter your Alpaca API Key and Secret Key in the sidebar.")
    elif not ticker:
        st.error("Please enter a ticker symbol.")
    elif selected_date.weekday() >= 5:
        st.error("The selected date is a weekend. Please pick a weekday (Mon–Fri).")
    else:
        with st.spinner(f"Fetching 1-minute bars for **{ticker}** on {selected_date}..."):
            try:
                df = fetch_bars(api_key, secret_key, ticker, selected_date)

                if df.empty:
                    st.warning(
                        f"No data returned for **{ticker}** on {selected_date}. "
                        "This may be a holiday, the market was closed, or the ticker had no trades."
                    )
                else:
                    ib_high, ib_low = compute_initial_balance(df)
                    bin_centers, volume_at_price, poc_price = compute_volume_profile(df, num_bins)

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Total Bars", f"{len(df)}")
                    col2.metric("IB High", f"${ib_high:.2f}")
                    col3.metric("IB Low", f"${ib_low:.2f}")
                    col4.metric("POC", f"${poc_price:.2f}")

                    fig = build_chart(df, ib_high, ib_low, bin_centers, volume_at_price, poc_price, ticker, selected_date)
                    st.plotly_chart(fig, use_container_width=True)

                    with st.expander("📋 Raw Bar Data"):
                        display_df = df[["open", "high", "low", "close", "volume"]].copy()
                        display_df.index = display_df.index.strftime("%H:%M")
                        display_df.columns = ["Open", "High", "Low", "Close", "Volume"]
                        st.dataframe(display_df, use_container_width=True)

            except Exception as e:
                err_msg = str(e)
                if "forbidden" in err_msg.lower() or "unauthorized" in err_msg.lower() or "403" in err_msg:
                    st.error("Authentication failed. Please check your API Key and Secret Key.")
                elif "not found" in err_msg.lower() or "invalid symbol" in err_msg.lower():
                    st.error(f"Ticker **{ticker}** was not found. Please check the symbol and try again.")
                else:
                    st.error(f"An error occurred: {err_msg}")
else:
    st.info("👈 Enter your Alpaca credentials and chart settings in the sidebar, then click **Fetch & Analyze**.")

    st.markdown("### How it works")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**📊 Volume Profile**")
        st.markdown("Price bins the entire day's 1-minute bars to show where the most volume traded.")
    with col2:
        st.markdown("**🎯 Point of Control (POC)**")
        st.markdown("The gold line marks the single price level with the highest volume — a key magnet for price.")
    with col3:
        st.markdown("**📐 Initial Balance (IB)**")
        st.markdown("The first 60 minutes of trading (9:30–10:30 EST) define the IB High and Low — key reference levels.")
