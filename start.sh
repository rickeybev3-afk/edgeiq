#!/bin/bash
# EdgeIQ VM Startup Script
# Runs the Paper Trader Bot in the background and Streamlit dashboard in the foreground.
# Both start together on every cold boot of the VM deployment.

# Change to the script's directory (project root)
cd "$(dirname "$0")"

# ── Paper Trader Bot ──────────────────────────────────────────────────────────
# Start as a background process so the bot runs its full scheduler independently.
# Note: /tmp is ephemeral on VM restarts; logs are for the current session only.
nohup python3 paper_trader_bot.py >> /tmp/paper_trader_bot.log 2>&1 &
BOT_PID=$!
echo "[start.sh] Paper Trader Bot started (PID: $BOT_PID)"

# ── Streamlit Dashboard ───────────────────────────────────────────────────────
# Run in the foreground — this keeps the VM process alive.
# The deployment health-check pings port 8080; Streamlit must bind here.
echo "[start.sh] Starting Streamlit dashboard on port 8080..."
exec python3 -m streamlit run app.py --server.port 8080
