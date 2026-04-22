#!/bin/bash
cd "$(dirname "$0")"
SERVE_PORT="${PORT:-8080}"

echo "[start.sh] Starting paper_trader_bot.py in background..."
python3 paper_trader_bot.py >> /tmp/paper_trader_bot.log 2>&1 &
BOT_PID=$!
echo "[start.sh] Paper trader bot started (PID $BOT_PID)"

echo "[start.sh] Starting deploy_server on port $SERVE_PORT..."
exec python3 deploy_server.py
