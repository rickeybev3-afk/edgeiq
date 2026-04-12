#!/bin/bash
cd /home/runner/workspace

nohup python3 paper_trader_bot.py >> /tmp/paper_trader_bot.log 2>&1 &
nohup python3 kalshi_bot.py >> /tmp/kalshi_bot.log 2>&1 &

export STREAMLIT_PORT=8501
nohup python3 -m streamlit run app.py \
  --server.port "$STREAMLIT_PORT" \
  --server.headless true \
  --server.address 127.0.0.1 \
  >> /tmp/streamlit.log 2>&1 &

sleep 5

exec node artifacts/api-server/dist/index.mjs
