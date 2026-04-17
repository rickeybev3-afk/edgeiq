# EdgeIQ — Professional Trading Terminal

## Overview

EdgeIQ is a Python Streamlit trading terminal designed for Volume Profile and Initial Balance (IB) structure analysis of small-cap stocks, primarily focusing on IB breakouts. It integrates with the Alpaca API for real-time data and trading, and Supabase for multi-user authentication and cloud data storage.

The core purpose is to identify and automate trading strategies based on structural market inefficiencies, specifically IB breakouts in high-TCS (Total Composite Score) small-cap setups. The long-term vision is to develop cognitive profiling software and a "Brain Marketplace" where verified traders can rent out their calibrated trading algorithms, fostering a consistency flywheel where logging trades leads to better calibration and passive income for traders. This marketplace will feature personalized brain matching based on cognitive fingerprinting.

The project is currently in Phase 1.5, focusing on paper execution of trades via Alpaca, with an upcoming transition to live execution (Phase 2). Financial projections indicate significant potential returns, with a strong emphasis on risk management and compounding.

## User Preferences

- **Timezone: EST always** — user is in Eastern Standard Time. All time references, scan schedules, and market hours must be in ET. Never say "UTC" or calculate wrong offsets.
- Build mode only, concise replies, no "ready for review" sign-offs
- Mobile-friendly communication style

## System Architecture

**Core Principles:**
- **Separation of Concerns:** Mathematical and logical operations are strictly confined to `backend.py`, while all UI/rendering functionalities reside in `app.py`.
- **IP Preservation:** Three critical functions (`compute_buy_sell_pressure`, `classify_day_structure`, `compute_structure_probabilities` in `backend.py`) are proprietary and must never be modified.
- **Plotting Standards:** Plotly/HTML elements use 6-digit hex or `rgba()` for colors.
- **User ID Handling:** `st.session_state.get("auth_user_id", "")` must be used for `USER_ID` within `app.py` render functions.

**Key Features & Implementations:**

1.  **IB Engine & TCS Scoring:** `backend.py` houses the core logic for Initial Balance calculations, Total Composite Score (TCS) assignments, and structure probability computations.
2.  **Adaptive Brains:** The system utilizes two brain files (`brain_weights.json` for live personal weights and `brain_weights_historical.json` for historical priors), which are blended at runtime. TCS thresholds are stored in `tcs_thresholds.json`.
3.  **Automated Trading Bot:** `paper_trader_bot.py` orchestrates daily autonomous operations, including watchlist refreshes, morning/intraday scans, Telegram alerts, Alpaca bracket order placement, EOD outcome resolution, and nightly brain recalibration. The bot operates on a defined schedule (9:15 AM, 10:47 AM, 11:45 AM, 2:00 PM, 4:20 PM, 4:25 PM, 4:30 PM, 11:59 PM ET).
4.  **Alpaca Execution Layer:** Integrated for automated bracket order placement (entry, stop loss, take profit) based on IB levels. Supports both paper and live trading via configuration.
5.  **P&L Simulation:** Three simulation scenarios (`pnl_r_sim` for MFE, `eod_pnl_r` for EOD hold, `tiered_pnl_r` for 50/25/25 ladder) are implemented in `backend.py` to evaluate strategy performance.
6.  **UI/UX:** Streamlit is used for the front-end, featuring dark mode and Plotly charts. Interactive filter simulation (`/filter_sim`) allows users to adjust parameters and view historical performance.
7.  **Data Column Semantics:** Strict definitions for data columns like `predicted`, `actual_outcome`, `win_loss`, `follow_thru_pct`, `pnl_r_sim`, `eod_pnl_r`, `tiered_pnl_r`, `close_price`, `ib_range_pct`, and `vwap_at_ib` are enforced to ensure data integrity and correct interpretation.

## External Dependencies

-   **Alpaca API:** Used for real-time market data (SIP feed) and automated trade execution (bracket orders) in both paper and live accounts.
-   **Supabase:** Serves as the backend for multi-user authentication and cloud data storage, managing `paper_trades` and `backtest_sim_runs` tables. Row Level Security (RLS) is permanently enabled.
-   **Streamlit:** The primary framework for building the web-based user interface.
-   **Plotly:** Utilized for interactive charting and data visualization within the Streamlit application.
-   **Pandas & NumPy:** Core libraries for data manipulation and numerical operations.
-   **PyTZ:** For timezone handling, ensuring all operations are aligned with EST.
-   **Alpaca-py & Supabase-py:** Python client libraries for interacting with the respective APIs.
-   **Telegram:** Integrated for sending alerts.