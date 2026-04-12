#!/bin/bash
cd /home/runner/workspace
nohup python3 paper_trader_bot.py >> /tmp/paper_trader_bot.log 2>&1 &
nohup python3 kalshi_bot.py >> /tmp/kalshi_bot.log 2>&1 &
exec python3 proxy_server.py
