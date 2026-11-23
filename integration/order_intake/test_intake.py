"""Integration tests for the order intake service against the live local ERPNext site.

Run:  ../../.venv/bin/python -m pytest -q test_intake.py
"""
import importlib
import json
import os
import sqlite3
import uuid

import pytest
import requests
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def svc(tmp_path_factory):
    os.environ["INTAKE_DB"] = str(tmp_path_factory.mktemp("intake") / "intake.db")
    os.environ["INTAKE_WORKER"] = "0"           # tests drive the retry loop explicitly
    import app as module
    module = importlib.reload(module)
    with TestClient(module.app) as client:
        yield module, client


def erp_orders_with_ref(module, key):
    r = requests.get(f"{module.ERP_URL}/api/resource/Sales Order", headers=module.erp_headers(),
                     params={"filters": json.dumps([["custom_intake_ref", "=", key], ["docstatus", "<", 2]]),
                             "fields": '["name","customer","docstatus","custom_retailer_staff"]'}, timeout=15)
    return r.json()["data"]


def order(key, item="WRD-3D", qty=2):
    return {"idempotency_key": key, "lines": [{"item_code": item, "qty": qty}]}


def test_order_creates_confirmed_sales_order(svc):
    module, client = svc
    key = uuid.uuid4().hex
    r = client.post("/api/orders", json=order(key), headers={"X-Staff-Token": "PADI-02"})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    [so] = erp_orders_with_ref(module, key)
    assert so["name"] == r.json()["erp_order"]
    assert so["customer"] == "Mega Home Stores - Padi" and so["docstatus"] == 1
    assert so["custom_retailer_staff"] == "Padi floor staff 2"


def test_double_tap_creates_one_order(svc):
    module, client = svc
    key = uuid.uuid4().hex
    first = client.post("/api/orders", json=order(key), headers={"X-Staff-Token": "PADI-01"}).json()
    second = client.post("/api/orders", json=order(key), headers={"X-Staff-Token": "PADI-01"}).json()
    assert first["erp_order"] == second["erp_order"]
    assert len(erp_orders_with_ref(module, key)) == 1


def test_unknown_item_rejected_before_erp(svc):
    _, client = svc
    r = client.post("/api/orders", json=order(uuid.uuid4().hex, item="SOFA-9000"), headers={"X-Staff-Token": "PADI-01"})
    assert r.status_code == 422 and "unknown item" in r.json()["detail"]


def test_unknown_staff_token_401(svc):
    _, client = svc
    r = client.post("/api/orders", json=order(uuid.uuid4().hex), headers={"X-Staff-Token": "NOPE-99"})
    assert r.status_code == 401


@pytest.mark.parametrize("qty", [0, 21])
def test_quantity_bounds(svc, qty):
    _, client = svc
    r = client.post("/api/orders", json=order(uuid.uuid4().hex, qty=qty), headers={"X-Staff-Token": "PADI-01"})
    assert r.status_code == 422


def test_erp_outage_queues_then_delivers_once(svc):
    module, client = svc
    key = uuid.uuid4().hex
    real = os.environ.get("ERP_URL", module.ERP_URL)
    os.environ["ERP_URL"] = "http://localhost:9"               # nothing listens here: simulated outage
    try:
        r = client.post("/api/orders", json=order(key, item="MND-5FT", qty=3), headers={"X-Staff-Token": "PALLI-01"})
        assert r.status_code == 200 and r.json()["status"] == "queued"
        assert module.row(key)["attempts"] == 1 and module.row(key)["last_error"]
    finally:
        os.environ["ERP_URL"] = real
    with sqlite3.connect(module.DB_PATH) as con:                # make the retry due now
        con.execute("UPDATE orders SET next_try = 0 WHERE key = ?", (key,))
    module.flush_queue()
    assert module.row(key)["status"] == "sent"
    assert len(erp_orders_with_ref(module, key)) == 1
    assert client.get(f"/api/orders/{key}").json()["erp_order"] == module.row(key)["erp_order"]


def test_crash_after_erp_write_is_still_exactly_once(svc):
    """ERPNext created the order but the service died before recording it: the replay must not duplicate."""
    module, client = svc
    key = uuid.uuid4().hex
    created = module.create_sales_order(key, "Mega Home Stores - Padi", [{"item_code": "OFT-5FT", "qty": 1}], "Padi floor staff 3")
    r = client.post("/api/orders", json=order(key, item="OFT-5FT", qty=1), headers={"X-Staff-Token": "PADI-03"})
    assert r.json()["erp_order"] == created
    assert len(erp_orders_with_ref(module, key)) == 1
