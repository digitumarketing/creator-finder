#!/usr/bin/env bash
# Start Creator Finder in the background (GitHub Codespaces / any cloud box).
# Safe to run again: it restarts the app. Log: /tmp/creator-finder.log
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "Installing dependencies (first run, ~1 min)…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
PIDFILE=/tmp/creator-finder.pid
if [[ -f $PIDFILE ]]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  sleep 1
fi
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8787}" \
  > /tmp/creator-finder.log 2>&1 &
echo $! > "$PIDFILE"
for _ in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:${PORT:-8787}/health" > /dev/null; then
    echo "Creator Finder is running on port ${PORT:-8787}. Open the Ports tab → ${PORT:-8787} → globe icon."
    exit 0
  fi
  sleep 1
done
echo "App didn't start. Last lines of /tmp/creator-finder.log:"
tail -30 /tmp/creator-finder.log
exit 1
