"""Production and material planning (phase 2), run on the as-is October books.

1. Reorder points from actual October consumption:
      reorder point = daily usage × supplier lead time (working days) + safety stock (4 days of usage)
      order qty     = 15 working days of usage, rounded up to 10 sheets
   Written to data/reorder_policy.json and to each item's reorder levels in ERPNext.
2. MRP for the open order book: a Production Plan over November's open Sales Orders works out the
   finished goods to make, explodes the BOMs into sheet/hardware requirements net of stock, and raises
   Material Requests for the shortfall.
"""
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import catalog as C
from erp import BASE, ERP, sql

A = C.ABBR
ROOT = Path(__file__).resolve().parent.parent
erp = ERP()
WORKING_DAYS_OCT = 25
GUJARAT_LEAD_WD = 10          # 12 calendar days ≈ 10 working days
SAFETY_DAYS = 4
COVER_DAYS = 15
RM_WH, FG_WH = f"Raw Material Store - {A}", f"Finished Goods Store - {A}"


def reorder_points():
    rows = sql(f"""
        SELECT sle.item_code, -SUM(sle.actual_qty) used
        FROM `tabStock Ledger Entry` sle JOIN `tabItem` i ON i.name = sle.item_code
        WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Stock Entry' AND sle.actual_qty < 0
          AND sle.warehouse = '{RM_WH}' AND i.item_group IN ('Boards', 'Laminates')
          AND sle.posting_date BETWEEN '2026-10-01' AND '2026-10-31'
        GROUP BY sle.item_code""")
    policy = {}
    for r in rows:
        daily = float(r["used"]) / WORKING_DAYS_OCT
        if daily < 0.2:
            continue
        rop = math.ceil(daily * (GUJARAT_LEAD_WD + SAFETY_DAYS))
        qty = int(math.ceil(daily * COVER_DAYS / 10.0) * 10)
        policy[r["item_code"]] = {"daily_usage": round(daily, 2), "reorder_point": rop, "order_qty": qty}
        item = erp.get("Item", r["item_code"])
        item["reorder_levels"] = [{"warehouse": RM_WH, "warehouse_reorder_level": rop, "warehouse_reorder_qty": qty,
                                   "material_request_type": "Purchase"}]
        erp.update("Item", r["item_code"], {"reorder_levels": item["reorder_levels"]})
    (ROOT / "data" / "reorder_policy.json").write_text(json.dumps(policy, indent=2))
    return policy


def november_orders():
    """The open order book on 2 Nov: phone orders taken but not yet built."""
    existing = erp.list("Sales Order", filters={"transaction_date": "2026-11-02", "docstatus": 1}, fields=["name"])
    if existing:
        return [s["name"] for s in existing]
    random.seed(11)
    taxes = erp.call("erpnext.controllers.accounts_controller.get_taxes_and_charges",
                     master_doctype="Sales Taxes and Charges Template", master_name=f"GST 18% In-State - {A}")
    book = [("Mega Home Stores - Padi", [("WRD-3D", 4), ("COT-STOR-Q", 3)]),
            ("Mega Home Stores - Padi", [("WRD-4D", 2), ("MND-5FT", 3)]),
            ("Southside Furnishings - Pallikaranai", [("WRD-2D", 3), ("COT-STOR-K", 2)]),
            ("Kanchi Furniture Mart", [("MND-7FT", 2), ("OFT-5FT", 4)]),
            ("Kovai Home Centre", [("WRD-3D", 3), ("COT-BASIC-Q", 3)])]
    names = []
    for cust, lines in book:
        so = erp.submit({"doctype": "Sales Order", "customer": cust, "company": C.COMPANY,
                         "transaction_date": "2026-11-02", "delivery_date": "2026-11-09", "set_warehouse": FG_WH,
                         "custom_order_kind": "Standard", "taxes_and_charges": f"GST 18% In-State - {A}", "taxes": taxes,
                         "items": [{"item_code": c, "qty": q, "delivery_date": "2026-11-09", "warehouse": FG_WH}
                                   for c, q in lines]})
        names.append(so["name"])
    return names


def erp_base():
    return BASE


def production_plan(so_names):
    default_bom = {b["item"]: b["name"] for b in erp.list("BOM", filters={"is_default": 1, "docstatus": 1},
                                                           fields=["name", "item"])}
    po_items, sos = [], []
    for so_name in so_names:
        so = erp.get("Sales Order", so_name)
        sos.append({"sales_order": so_name, "customer": so["customer"], "sales_order_date": so["transaction_date"],
                    "grand_total": so["grand_total"]})
        for it in so["items"]:
            po_items.append({"item_code": it["item_code"], "bom_no": default_bom[it["item_code"]],
                             "planned_qty": it["qty"], "pending_qty": it["qty"], "warehouse": FG_WH,
                             "sales_order": so_name, "sales_order_item": it["name"], "stock_uom": "Nos",
                             "planned_start_date": "2026-11-03 09:00:00"})
    for old in erp.list("Production Plan", filters={"posting_date": "2026-11-02"}, fields=["name", "docstatus"]):
        if old["docstatus"] == 1:
            erp.call("frappe.client.cancel", doctype="Production Plan", name=old["name"])
        erp.s.delete(f"{erp_base()}/api/resource/Production Plan/{old['name']}")
    # ignore_existing_ordered_qty=1 makes ERPNext net requirements against stock in each item's default store
    doc = {"doctype": "Production Plan", "company": C.COMPANY, "posting_date": "2026-11-02",
           "get_items_from": "Sales Order", "sales_orders": sos, "po_items": po_items,
           "combine_items": 1, "ignore_existing_ordered_qty": 1, "include_safety_stock": 0}
    mr = erp.call("erpnext.manufacturing.doctype.production_plan.production_plan.get_items_for_material_requests",
                  doc=json.dumps(doc))
    doc["mr_items"] = mr
    pp = erp.submit(doc)
    return pp, mr


def make_material_requests(pp):
    r = erp.s.post(f"{BASE}/api/method/run_doc_method",
                   json={"dt": "Production Plan", "dn": pp["name"], "method": "make_material_request"})
    erp._check(r)
    return [{"name": r["parent"]} for r in sql(
        f"SELECT DISTINCT parent FROM `tabMaterial Request Item` WHERE production_plan = '{pp['name']}'")]


def main():
    policy = reorder_points()
    print("reorder policy:", json.dumps(policy, indent=1))
    pp, mr = production_plan(november_orders())
    agg = {}
    for m in mr:
        a = agg.setdefault(m["item_code"], {"required": 0.0, "in_stock": float(m.get("actual_qty") or 0), "to_request": 0.0})
        a["required"] += float(m.get("required_bom_qty") or 0)
        a["to_request"] += float(m.get("quantity") or 0)
    mrs = make_material_requests(pp)
    print("production plan", pp["name"], "->", len(pp["po_items"]), "finished-good lines;",
          sum(1 for a in agg.values() if a["to_request"] > 0), "materials short; material requests:",
          [m["name"] for m in mrs])
    for code, a in sorted(agg.items(), key=lambda kv: -kv[1]["to_request"]):
        print(f"  {code:16s} required {a['required']:8.1f}  in stock {a['in_stock']:8.1f}  to request {a['to_request']:8.1f}")
    (ROOT / "out" / "production_plan.json").write_text(json.dumps(
        {"plan": pp["name"], "sales_orders": [s["sales_order"] for s in pp["sales_orders"]],
         "finished_goods": [(i["item_code"], i["planned_qty"]) for i in pp["po_items"]],
         "materials": agg, "material_requests": [m["name"] for m in mrs], "reorder_policy": policy},
        indent=1, default=str))


if __name__ == "__main__":
    main()
