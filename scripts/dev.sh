#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo 'Run uv venv --python 3.14 && uv pip install --python .venv/bin/python -r requirements.txt first.'
  exit 1
fi
if [ ! -d frontend/node_modules ]; then (cd frontend && npm ci); fi
trap 'kill "$api_pid" "$web_pid" 2>/dev/null || true' EXIT INT TERM
PYTHONPATH=backend .venv/bin/uvicorn presscheck.api:app --host 127.0.0.1 --port 8000 &
api_pid=$!
(cd frontend && npm run dev -- --port 3000) &
web_pid=$!
wait
