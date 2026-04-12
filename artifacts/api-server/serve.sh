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

echo "[serve.sh] Waiting for Streamlit to be ready on port $STREAMLIT_PORT..."
for i in $(seq 1 60); do
  if curl -s -o /dev/null -w '' "http://127.0.0.1:$STREAMLIT_PORT/_stcore/health" 2>/dev/null; then
    echo "[serve.sh] Streamlit is ready after ${i}s"
    break
  fi
  sleep 1
done

exec node artifacts/api-server/dist/index.mjs
