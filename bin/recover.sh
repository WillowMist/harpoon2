#!/usr/bin/env bash
# bin/recover.sh — stop celery, flush stale Redis tasks, restart celery.
# Use after a duplicate-dispatch incident or any time celery is STOPPED
# with tasks pending in the broker.
set -euo pipefail

echo "[recover] Stopping celery-worker and celery-beat..."
docker exec harpoon2 supervisorctl stop celery-worker celery-beat

echo "[recover] Flushing Redis DB 0 (celery broker)..."
docker exec harpoon2-redis redis-cli FLUSHDB

echo "[recover] Restarting celery-worker and celery-beat..."
docker exec harpoon2 supervisorctl start celery-worker celery-beat

sleep 5

echo "[recover] Active task count:"
docker exec harpoon2 celery -A harpoon2 inspect active 2>/dev/null \
  | grep -c "^\s*{" || echo "0"

echo "[recover] Done. Verify dashboard at http://<host>:4277/"