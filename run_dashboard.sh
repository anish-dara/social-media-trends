#!/usr/bin/env bash
# Start (or restart) the TrendRadar dashboard on http://localhost:8501
# Run this whenever the browser says "connection refused" -- it just means the
# Streamlit process isn't running (it stops when the terminal closes or the Mac
# sleeps; it does not auto-restart).
set -e
cd "$(dirname "$0")"

PORT=8501

# Kill any existing dashboard on the port so we always get a clean start.
EXISTING=$(lsof -ti :$PORT || true)
if [ -n "$EXISTING" ]; then
  echo "Stopping existing dashboard (pid $EXISTING)..."
  kill $EXISTING || true
  sleep 1
fi

echo "Starting dashboard..."
nohup .venv/bin/streamlit run dashboard/app.py \
  --server.headless true --server.port $PORT \
  > /tmp/trendradar_dashboard.log 2>&1 &
disown

# Wait until it answers, up to ~15s.
for i in $(seq 1 15); do
  if curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT | grep -q 200; then
    echo "✅ Dashboard is up: http://localhost:$PORT"
    exit 0
  fi
  sleep 1
done

echo "⚠️ Dashboard did not come up in time. Check the log:"
echo "   tail -30 /tmp/trendradar_dashboard.log"
exit 1
