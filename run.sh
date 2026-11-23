#!/usr/bin/env bash
# Rebuild the whole implementation from scratch on a local machine (Docker + Python 3.11+).
set -euo pipefail
cd "$(dirname "$0")"

# 1. ERPNext v16 via the official frappe_docker quick-start compose file
[ -d frappe_docker ] || git clone --depth 1 https://github.com/frappe/frappe_docker.git
docker compose -p kfw-erp -f frappe_docker/pwd.yml up -d
until [ "$(docker inspect -f '{{.State.Status}}' kfw-erp-create-site-1 2>/dev/null)" = "exited" ]; do sleep 5; done
until curl -sf http://localhost:8080/api/method/ping >/dev/null; do sleep 3; done

# 2. Python environment
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# 3. Implementation
cd scripts
PY=../.venv/bin/python
[ -f ../data/master_data.xlsx ] || $PY seed_master_workbook.py   # synthetic values; replace with real ones to go live
$PY gen_legacy_data.py      # synthetic 'accountant Excel' files to migrate
$PY setup_erp.py            # configure ERPNext from data/master_data.xlsx
$PY migrate.py              # load opening data, write exceptions
$PY reconcile.py            # SQL proof that ERP matches the sources
$PY simulate.py             # run October 2026 day by day as real documents
$PY reports.py              # owner reports + dashboard
$PY results.py              # docs/06-results-october.md from the books
$PY planning.py             # reorder points from consumption + MRP over the open order book
$PY approvals.py            # PO approval workflow (after the simulated month)
$PY notifications.py        # in-app alerts: new orders, POs awaiting approval
( cd ../integration/order_intake && ../../.venv/bin/pip install -q -r requirements.txt && ../../.venv/bin/python setup_integration.py )
$PY uat.py                  # 18 UAT cases -> docs/08-uat-test-cases.md
# to-be comparison (optional, needs a DB snapshot taken after migrate.py; see restore_snapshot.sh):
#   ./restore_snapshot.sh snapshots/post_migration.sql && POLICY=reorder $PY simulate.py && $PY planning_doc.py
echo "Done. Open http://localhost:8080 (Administrator / admin, local only) -> Owner Dashboard"
