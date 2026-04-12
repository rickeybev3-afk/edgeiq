#!/bin/bash
cd "$(dirname "$0")"
SERVE_PORT="${PORT:-8080}"
echo "[start.sh] Starting deploy_server on port $SERVE_PORT..."
exec python3 deploy_server.py
