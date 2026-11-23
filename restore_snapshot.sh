#!/usr/bin/env bash
# Restore the post-migration database snapshot (lets the October simulation be re-run from a clean state).
set -euo pipefail
SNAP="${1:?usage: restore_snapshot.sh <dump.sql>}"
docker exec -i kfw-erp-db-1 mariadb -uroot -padmin < "$SNAP"
docker exec kfw-erp-redis-cache-1 redis-cli FLUSHALL >/dev/null
docker exec kfw-erp-backend-1 bench --site frontend clear-cache
# fresh workers: cached auth/encryption state from before the restore otherwise breaks API-key logins
docker restart kfw-erp-backend-1 >/dev/null && docker restart kfw-erp-frontend-1 >/dev/null
until curl -sf http://localhost:8080/api/method/ping >/dev/null; do sleep 2; done
