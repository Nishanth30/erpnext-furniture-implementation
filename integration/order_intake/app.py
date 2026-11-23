"""Retail order intake: company sales staff on a retailer's floor send orders from a phone; the service
creates confirmed Sales Orders in ERPNext through its REST API.

Guarantees
  * validated before ERPNext sees it (staff token -> retailer, known finished goods, sane quantities)
  * exactly-once: every order carries an idempotency key; a double tap, a resend after a timeout, or a
    retry after a crash never creates a second Sales Order (local unique key + ERPNext-side lookup on
    the custom field custom_intake_ref)
  * never lost: orders are written to a local SQLite outbox first; if ERPNext is unreachable they stay
    queued and a background worker retries with exponential backoff

Endpoints
  GET  /                      mobile order form for floor staff
  POST /order                 form submit (HTML)
  POST /api/orders            JSON API for other systems (header X-Staff-Token)
  GET  /api/orders/{key}      status of one order
"""
import html
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from fastapi import FastAPI, Form, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent


def _load_env():
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
ERP_URL = os.environ.get("ERP_URL", "http://localhost:8080")
DB_PATH = Path(os.environ.get("INTAKE_DB", HERE / "intake.db"))
STAFF = json.loads((HERE / "staff.json").read_text())        # token -> {name, retailer}
COMPANY = os.environ.get("ERP_COMPANY", "Kaveri Furniture Works")
FG_WAREHOUSE = os.environ.get("ERP_FG_WAREHOUSE", "Finished Goods Store - KFW")
TAX_TEMPLATE = os.environ.get("ERP_TAX_TEMPLATE", "GST 18% In-State - KFW")
MAX_QTY = 20
RETRY_BASE_SECONDS = 15
LEAD_DAYS = 7


class ERPUnavailable(Exception):
    """Network failure or 5xx: safe to retry later."""


class ERPRejected(Exception):
    """ERPNext refused the document (4xx): retrying will not help."""


# ------------------------------------------------------------------ ERPNext client (token auth, integration user)
def erp_headers():
    return {"Authorization": f"token {os.environ['ERP_API_KEY']}:{os.environ['ERP_API_SECRET']}",
            "Accept": "application/json"}


def erp_request(method, path, **kw):
    try:
        r = requests.request(method, f"{os.environ.get('ERP_URL', ERP_URL)}{path}", headers=erp_headers(),
                             timeout=15, **kw)
    except requests.RequestException as e:
        raise ERPUnavailable(str(e)) from e
    if r.status_code >= 500:
        raise ERPUnavailable(f"{r.status_code} {r.text[:200]}")
    if r.status_code >= 400:
        msg = r.text
        try:
            body = r.json()
            if "_server_messages" in body:
                msg = " | ".join(json.loads(m).get("message", m) for m in json.loads(body["_server_messages"]))
            else:
                msg = body.get("exception", msg)
        except ValueError:
            pass
        raise ERPRejected(f"{r.status_code}: {msg[:300]}")
    return r.json()


_catalog = {"at": 0.0, "items": {}}


def sellable_items():
    """Finished goods that can be ordered (cached 10 minutes)."""
    if time.time() - _catalog["at"] > 600:
        flt = json.dumps([["is_sales_item", "=", 1], ["disabled", "=", 0],
                          ["item_group", "in", ["Wardrobes", "Cots", "Pooja Mandirs", "Office Tables", "Scrap Products"]]])
        data = erp_request("GET", "/api/resource/Item", params={"filters": flt, "fields": '["name","item_name"]',
                                                                   "limit_page_length": 500})["data"]
        _catalog.update(at=time.time(), items={d["name"]: d["item_name"] for d in data})
    return _catalog["items"]


def create_sales_order(key, retailer, lines, staff_name):
    """Idempotent on ERPNext side: if a Sales Order already carries this intake key, return it."""
    found = erp_request("GET", "/api/resource/Sales Order",
                        params={"filters": json.dumps([["custom_intake_ref", "=", key], ["docstatus", "<", 2]]),
                                "fields": '["name"]'})["data"]
    if found:
        return found[0]["name"]
    taxes = erp_request("POST", "/api/method/erpnext.controllers.accounts_controller.get_taxes_and_charges",
                        json={"master_doctype": "Sales Taxes and Charges Template", "master_name": TAX_TEMPLATE})["message"]
    due = (date.today() + timedelta(days=LEAD_DAYS)).isoformat()
    so = {"doctype": "Sales Order", "customer": retailer, "company": COMPANY, "order_type": "Sales",
          "transaction_date": date.today().isoformat(), "delivery_date": due, "set_warehouse": FG_WAREHOUSE,
          "custom_order_kind": "Standard", "custom_intake_ref": key, "custom_retailer_staff": staff_name,
          "taxes_and_charges": TAX_TEMPLATE, "taxes": taxes,
          "items": [{"item_code": l["item_code"], "qty": l["qty"], "delivery_date": due, "warehouse": FG_WAREHOUSE}
                    for l in lines]}
    doc = erp_request("POST", "/api/resource/Sales Order", json=so)["data"]
    erp_request("POST", "/api/method/frappe.client.submit", json={"doc": doc})
    return doc["name"]


# ------------------------------------------------------------------ outbox
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with closing(db()) as con, con:
        con.execute("""CREATE TABLE IF NOT EXISTS orders (
            key TEXT PRIMARY KEY, staff TEXT NOT NULL, retailer TEXT NOT NULL, lines TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'sent', 'rejected')),
            erp_order TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
            last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")


def row(key):
    with closing(db()) as con:
        r = con.execute("SELECT * FROM orders WHERE key = ?", (key,)).fetchone()
    return dict(r) if r else None


def enqueue(key, staff_token, lines):
    staff = STAFF[staff_token]
    now = datetime.now().isoformat(timespec="seconds")
    with closing(db()) as con, con:
        try:
            con.execute("INSERT INTO orders (key, staff, retailer, lines, status, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                        (key, staff["name"], staff["retailer"], json.dumps(lines), now, now))
            return True
        except sqlite3.IntegrityError:
            return False          # same key seen before: idempotent replay


def attempt(key):
    """Try to push one queued order to ERPNext. Concurrency-safe: only a claimed row is sent."""
    r = row(key)
    if not r or r["status"] != "queued":
        return r
    with closing(db()) as con, con:
        claimed = con.execute("UPDATE orders SET next_try = ? WHERE key = ? AND status = 'queued' AND next_try <= ?",
                              (time.time() + 60, key, time.time())).rowcount
    if not claimed:
        return row(key)
    status, erp_order, err = "queued", None, None
    try:
        erp_order = create_sales_order(key, r["retailer"], json.loads(r["lines"]), r["staff"])
        status = "sent"
    except ERPRejected as e:
        status, err = "rejected", str(e)
    except ERPUnavailable as e:
        err = str(e)
    attempts = r["attempts"] + 1
    next_try = time.time() + RETRY_BASE_SECONDS * 2 ** min(attempts - 1, 6)
    with closing(db()) as con, con:
        con.execute("UPDATE orders SET status = ?, erp_order = ?, attempts = ?, next_try = ?, last_error = ?, "
                    "updated_at = ? WHERE key = ?",
                    (status, erp_order, attempts, next_try if status == "queued" else 0, err,
                     datetime.now().isoformat(timespec="seconds"), key))
    return row(key)


def flush_queue():
    with closing(db()) as con:
        due = [r["key"] for r in con.execute("SELECT key FROM orders WHERE status = 'queued' AND next_try <= ?",
                                             (time.time(),))]
    return [attempt(k) for k in due]


def worker(stop):
    while not stop.wait(5):
        try:
            flush_queue()
        except Exception as e:          # keep the worker alive; the row keeps its last_error
            print("intake worker:", e)


# ------------------------------------------------------------------ API
class Line(BaseModel):
    item_code: str
    qty: int = Field(ge=1, le=MAX_QTY)


class OrderIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=64)
    lines: list[Line] = Field(min_length=1, max_length=10)


def validate(lines):
    try:
        items = sellable_items()
    except ERPUnavailable:
        items = _catalog["items"]        # ERPNext down: validate against the last known catalog
    unknown = [l.item_code for l in lines if l.item_code not in items] if items else []
    if unknown:
        raise HTTPException(422, f"unknown item(s): {', '.join(unknown)}")
    return [{"item_code": l.item_code, "qty": l.qty} for l in lines]


def submit_order(order: OrderIn, token: str):
    if token not in STAFF:
        raise HTTPException(401, f"staff code '{token}' is not recognised. Use only the code on your card, e.g. PADI-01")
    lines = validate(order.lines)
    enqueue(order.idempotency_key, token, lines)
    r = attempt(order.idempotency_key)
    if r["status"] == "rejected":
        raise HTTPException(422, f"ERPNext rejected the order: {r['last_error']}")
    return {"key": r["key"], "status": r["status"], "erp_order": r["erp_order"], "retailer": r["retailer"]}


@asynccontextmanager
async def lifespan(_app):
    init_db()
    stop = threading.Event()
    if os.environ.get("INTAKE_WORKER", "1") == "1":
        threading.Thread(target=worker, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Retail Order Intake", lifespan=lifespan)


@app.post("/api/orders")
def api_orders(order: OrderIn, x_staff_token: str = Header(...)):
    return submit_order(order, x_staff_token)


@app.get("/api/orders/{key}")
def api_order_status(key: str):
    r = row(key)
    if not r:
        raise HTTPException(404, "no such order")
    return {k: r[k] for k in ("key", "status", "erp_order", "retailer", "attempts", "last_error")}


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Order Intake</title><style>
body{{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#f4f5f7;color:#1f2933}}
main{{max-width:420px;margin:0 auto;padding:20px 16px}} h1{{font-size:20px;margin:4px 0 2px}}
.sub{{color:#52606d;font-size:13px;margin-bottom:16px}} .card{{background:#fff;border-radius:12px;padding:16px;
box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:14px}} label{{font-size:13px;color:#52606d;display:block;margin:10px 0 4px}}
select,input{{width:100%;padding:12px;font-size:16px;border:1px solid #cbd2d9;border-radius:8px;box-sizing:border-box;background:#fff}}
.row{{display:grid;grid-template-columns:1fr 84px;gap:8px}} button{{width:100%;padding:14px;font-size:17px;border:0;
border-radius:10px;background:#1f4e78;color:#fff;margin-top:6px}} .ok{{border-left:4px solid #2f9e44}}
.warn{{border-left:4px solid #f08c00}} .who{{font-size:14px;color:#323f4b;background:#eef2f7;
padding:10px 12px;border-radius:8px}} .who a{{color:#1f4e78;margin-left:6px}} .err{{border-left:4px solid #e03131}}</style></head><body><main>
<h1>New retailer order</h1><div class="sub">Kaveri Furniture Works · goes straight to the factory system</div>
{banner}<form method="post" action="/order" class="card">
<input type="hidden" name="key" value="{key}">{staff_field}
{rows}<button type="submit">Send order</button></form></main></body></html>"""


def staff_field(token):
    """Known code: locked and shown by name. Unknown/empty: an input that accepts only the code pattern."""
    t = token.strip().upper()
    if t in STAFF:
        who = STAFF[t]
        return (f'<input type="hidden" name="token" value="{html.escape(t)}"><div class="who">Sending as '
                f'<b>{html.escape(who["name"])}</b> · {html.escape(who["retailer"])} '
                f'<a href="/">change</a></div>')
    return (f'<label>Your staff code</label><input name="token" value="{html.escape(token)}" placeholder="e.g. PADI-01" '
            f'required autocomplete="off" autocapitalize="characters" pattern="[A-Za-z]+-[0-9]+" '
            f'title="Only the code on your card, e.g. PADI-01">')


def form_rows(items):
    opts = "".join(f'<option value="{html.escape(c)}">{html.escape(n)}</option>' for c, n in sorted(items.items(), key=lambda x: x[1]))
    return "".join(f'<div class="row"><div><label>Item {i + 1}</label><select name="item{i}">'
                   f'<option value="">{"Choose item" if i == 0 else "(optional)"}</option>{opts}</select></div>'
                   f'<div><label>Qty</label><input name="qty{i}" type="number" min="1" max="{MAX_QTY}" inputmode="numeric"></div></div>'
                   for i in range(3))


@app.get("/", response_class=HTMLResponse)
def form(token: str = ""):
    try:
        items = sellable_items()
    except ERPUnavailable:
        items = _catalog["items"]
    return PAGE.format(banner="", key=uuid.uuid4().hex, staff_field=staff_field(token), rows=form_rows(items))


@app.post("/order", response_class=HTMLResponse)
def form_submit(key: str = Form(...), token: str = Form(...), item0: str = Form(""), qty0: str = Form(""),
                item1: str = Form(""), qty1: str = Form(""), item2: str = Form(""), qty2: str = Form("")):
    lines = [Line(item_code=i, qty=int(q)) for i, q in ((item0, qty0), (item1, qty1), (item2, qty2)) if i and q]
    try:
        res = submit_order(OrderIn(idempotency_key=key, lines=lines), token.strip().upper())
        if res["status"] == "sent":
            banner = f'<div class="card ok"><b>Order {res["erp_order"]} created</b><br>{html.escape(res["retailer"])}</div>'
        else:
            banner = ('<div class="card warn"><b>Saved, will send automatically</b><br>The factory system is not '
                      'reachable right now. Do not re-enter this order.</div>')
    except (HTTPException, ValueError) as e:
        detail = e.detail if isinstance(e, HTTPException) else "choose at least one item with a quantity"
        banner = f'<div class="card err"><b>Not sent</b><br>{html.escape(str(detail))}</div>'
    try:
        items = sellable_items()
    except ERPUnavailable:
        items = _catalog["items"]
    return PAGE.format(banner=banner, key=uuid.uuid4().hex, staff_field=staff_field(token), rows=form_rows(items))
