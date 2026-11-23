"""Run October 2026 through ERPNext, day by day, the way the factory works today ("as-is").

Every event is a real ERPNext document: Sales Orders, Quality Inspections, Delivery Notes,
Sales Invoices, Payment Entries, Purchase Orders/Receipts/Invoices, Landed Cost Vouchers,
Manufacture Stock Entries (with scrap and absorbed operating cost), Material Issues for
transit-damage replacements, Production Downtime and Transit Damage logs, month-end
Journal Entries and a physical stock count.

As-is behaviours reproduced (from discovery with the owner):
  * sheets are bulk-ordered from Gujarat only after retailer payments free up cash
  * production follows the nearest delivery deadline; no supervisor, no plan
  * ~6% of units fail the pre-dispatch check (finish / handles / edge banding) -> rework
  * ~4% of delivered units are damaged in transit -> full free replacement
  * one CNC breakdown in the month (repair visit ~ Rs 10,000)
  * one retailer pays late; the largest retailer pays early and takes the discount
  * hardware goes missing; found only at the month-end count
  * custom walk-in wardrobe orders must pass the measurement + sign-off gate

Deterministic (fixed seed). Run once on a freshly migrated site (see reset.sh).
"""
import base64
import json
import os
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import catalog as C
from erp import BASE, ERP, ERPError

random.seed(2026)
# POLICY=asis   : sheets bulk-ordered only after retailer payments free up cash (how the factory works today)
# POLICY=reorder: sheets ordered when on-hand + on-order falls below a computed reorder point (planning.py)
POLICY = os.environ.get("POLICY", "asis")
A = C.ABBR
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
erp = ERP()

FG_WH, RM_WH, HW_WH, SCRAP_WH, WIP_WH = (f"{w} - {A}" for w in
                                          ("Finished Goods Store", "Raw Material Store", "Hardware Store",
                                           "Scrap Yard", "Work In Progress"))
BANK = f"HDFC Current Account - {A}"
CC = f"Main - {A}"
QI_TEMPLATE = "Pre-Dispatch Furniture Check"
SALES_TAX = f"GST 18% In-State - {A}"
HOLIDAYS = {date(2026, 10, 2), date(2026, 10, 20)}            # Gandhi Jayanti, Ayudha Pooja
CNC_BREAKDOWN = date(2026, 10, 14)
DEMAND_MIX = {"WRD-3D": 12, "WRD-2D": 8, "WRD-4D": 5, "COT-STOR-Q": 10, "COT-STOR-K": 6, "COT-BASIC-Q": 7,
              "MND-3X2": 12, "MND-5FT": 10, "MND-7FT": 5, "OFT-5FT": 10}
RETAILER_WEIGHT = [45, 20, 13, 12, 10]
PAY_DAYS = {0: (5, 7), 1: (10, 14), 2: (12, 16), 3: (14, 20), 4: (26, 40)}   # by retailer index
PARALLEL = {"Assembly Bay": 2, "Finishing Bay": 2}             # teams working in parallel
DEFECT_RATE, DAMAGE_RATE = 0.06, 0.04
QC_ITEMS = {c for c, *_ in C.FINISHED_GOODS}          # scrap products ship without the furniture QC
BULK_TRIGGER_CASH = 500_000
GUJARAT_LEAD, LOCAL_LEAD = 12, 2
GUJARAT_FREIGHT = 8_000
BULK_BASKET = {"PB-18-PRELAM": 170, "BRD-UV-18": 45, "PLY-18-COM": 60, "PLY-06-BACK": 50, "LAM-1MM-MARBLE": 30,
               "LAM-1MM-MATTE": 40, "LAM-08-LINER": 45, "PNL-GLOSS-PRINT": 20, "PB-18-RAW": 20}
LOCAL_PREMIUM = 1.08
WEEKLY_TOPUP = {  # supplier: {item: target qty}
    "Parrys Hardware Mart": {"HW-HINGE-SC": 300, "HW-HANDLE": 90, "HW-LOCK": 35, "HW-CHANNEL": 55,
                             "HW-ROD-SET": 25, "HW-SCREW-BOX": 30, "HW-MINIFIX": 1200, "HW-CASTOR": 40,
                             "HW-CLOCK-MECH": 20, "HW-TUBE-BRKT": 70, "EDGE-PVC-2MM": 1500, "ADH-PVA": 50,
                             "PKG-LARGE": 60, "PKG-SMALL": 80},
    "Ambattur Steel Tubes": {"MS-TUBE-25": 110},
    "Sowcarpet Upholstery Supplies": {"UPH-FOAM-40D": 14, "UPH-FABRIC": 35, "UPH-BUTTON": 500},
}

RATE = {c: r for c, _, _, _, r in C.RAW_MATERIALS}
GROUP = {c: g for c, _, g, _, _ in C.RAW_MATERIALS}
SUPPLIER_STATE = {n: s for n, _, s, _, _ in C.SUPPLIERS}
CUSTOMERS = [c[0] for c in C.CUSTOMERS]
CUST_IDX = {c: i for i, c in enumerate(CUSTOMERS)}
EARLY = {c[0]: c[4] for c in C.CUSTOMERS}
CREDIT = {c[0]: c[2] for c in C.CUSTOMERS}

log_lines = []
metrics = defaultdict(float)


def log(d, msg):
    line = f"{d.isoformat()}  {msg}"
    log_lines.append(line)
    print(line)


def iso(d):
    return d.isoformat()


def home_wh(code):
    if code in RATE:
        return HW_WH if GROUP[code] == "Hardware" else RM_WH
    if code.startswith("SCRAP-"):
        return SCRAP_WH
    if code.startswith("SA-"):
        return RM_WH
    return FG_WH


# ------------------------------------------------------------------ local stock mirror
stock = defaultdict(float)
for b in erp.list("Bin", fields=["item_code", "warehouse", "actual_qty"]):
    stock[(b["item_code"], b["warehouse"])] = float(b["actual_qty"])


def have(code):
    return stock[(code, home_wh(code))]


def move(code, qty):
    stock[(code, home_wh(code))] += qty


# ------------------------------------------------------------------ BOM + station data
BOM = {}
for b in erp.list("BOM", filters={"docstatus": 1, "is_active": 1}, fields=["name", "item", "is_default"]):
    doc = erp.get("BOM", b["name"])
    BOM[b["name"]] = doc
DEFAULT_BOM = {d["item"]: n for n, d in BOM.items() if d["is_default"]}
WS_COST = {}
for w in erp.list("Workstation", fields=["name"]):
    WS_COST[w["name"]] = {c["operating_component"]: c["operating_cost"]
                          for c in erp.get("Workstation", w["name"])["workstation_costs"]}
TAXES_SALES = erp.call("erpnext.controllers.accounts_controller.get_taxes_and_charges",
                       master_doctype="Sales Taxes and Charges Template", master_name=SALES_TAX)


def purchase_taxes(supplier):
    tpl = f"{'GST 18% In-State' if SUPPLIER_STATE[supplier] == C.STATE else 'IGST 18% Inter-State'} - {A}"
    return tpl, erp.call("erpnext.controllers.accounts_controller.get_taxes_and_charges",
                         master_doctype="Purchase Taxes and Charges Template", master_name=tpl)


# ------------------------------------------------------------------ production
station_used = defaultdict(float)


def capacity(ws, d):
    if ws == "CNC Router" and d == CNC_BREAKDOWN:
        return 0
    return C.SHIFT_HOURS * PARALLEL.get(ws, 1)


def bom_shortages(bom_name, qty):
    return {i["item_code"]: i["stock_qty"] * qty - have(i["item_code"])
            for i in BOM[bom_name]["items"] if have(i["item_code"]) < i["stock_qty"] * qty - 1e-9}


def stations_free(bom_name, qty, d):
    return all(station_used[op["workstation"]] + op["time_in_mins"] / 60 * qty <= capacity(op["workstation"], d) + 1e-9
               for op in BOM[bom_name]["operations"])


def manufacture(bom_name, qty, d):
    b = BOM[bom_name]
    items = [{"item_code": i["item_code"], "qty": round(i["stock_qty"] * qty, 4), "s_warehouse": home_wh(i["item_code"])}
             for i in b["items"]]
    items.append({"item_code": b["item"], "qty": qty, "t_warehouse": home_wh(b["item"]), "is_finished_item": 1})
    for s in b.get("secondary_items", []):
        items.append({"item_code": s["item_code"], "qty": round(s["stock_qty"] * qty, 4), "t_warehouse": SCRAP_WH,
                      "secondary_item_type": "Scrap", "valuation_type": "Valuation Rate"})
    comp = defaultdict(float)
    for op in b["operations"]:
        hrs = op["time_in_mins"] / 60 * qty
        station_used[op["workstation"]] += hrs
        for k, v in WS_COST[op["workstation"]].items():
            comp[k] += v * hrs
    se = erp.submit({"doctype": "Stock Entry", "stock_entry_type": "Manufacture", "purpose": "Manufacture",
                     "company": C.COMPANY, "from_bom": 1, "bom_no": bom_name, "fg_completed_qty": qty,
                     "posting_date": iso(d), "posting_time": "17:00:00", "set_posting_time": 1, "items": items,
                     "additional_costs": [{"expense_account": f"Expenses Included In Valuation - {A}",
                                           "description": f"Operating cost - {k}", "amount": round(v, 2),
                                           "has_operating_cost": 1, "operating_component": k}
                                          for k, v in comp.items()]})
    for i in b["items"]:
        move(i["item_code"], -i["stock_qty"] * qty)
    move(b["item"], qty)
    for s in b.get("secondary_items", []):
        move(s["item_code"], s["stock_qty"] * qty)
    metrics["absorbed_" + "operating"] += sum(comp.values())
    return se["name"]


wrd3d_toggle = [0]


def choose_bom(item):
    if item == "WRD-3D":          # make-or-buy trial: alternate bought UV door board vs in-house pressed
        wrd3d_toggle[0] ^= 1
        return "BOM-WRD-3D-002" if wrd3d_toggle[0] else "BOM-WRD-3D-001"
    return DEFAULT_BOM[item]


def try_make(item, qty, d):
    """Make qty units if materials and station hours allow; press door boards first if needed."""
    bom = choose_bom(item)
    if bom == "BOM-WRD-3D-002":
        need = 1.5 * qty - have("SA-DOOR-PRESSED")
        if need > 0:
            sa_qty = round(need + 0.49)
            sa_bom = DEFAULT_BOM["SA-DOOR-PRESSED"]
            if bom_shortages(sa_bom, sa_qty) or not stations_free(sa_bom, sa_qty, d):
                bom = "BOM-WRD-3D-001"
            else:
                manufacture(sa_bom, sa_qty, d)
                metrics["pressed_boards"] += sa_qty
    short = bom_shortages(bom, qty)
    if short:
        return None, short
    if not stations_free(bom, qty, d):
        return None, {"capacity": 1}
    manufacture(bom, qty, d)
    metrics[f"made::{bom}"] += qty
    return bom, {}


# ------------------------------------------------------------------ purchasing
in_transit = []          # (arrival date, PO name, supplier, gujarat?)
cash_since_bulk = [0.0]
last_local_buy = [date(2026, 9, 1)]


def place_po(supplier, lines, d, lead, rate_factor=1.0):
    tpl, taxes = purchase_taxes(supplier)
    arrive = d + timedelta(days=lead)
    po = erp.submit({"doctype": "Purchase Order", "supplier": supplier, "company": C.COMPANY,
                     "transaction_date": iso(d), "schedule_date": iso(arrive), "taxes_and_charges": tpl,
                     "taxes": taxes, "set_warehouse": RM_WH,
                     "items": [{"item_code": c, "qty": q, "rate": round(RATE[c] * rate_factor, 2),
                                "warehouse": home_wh(c), "schedule_date": iso(arrive)} for c, q in lines.items()]})
    gujarat = SUPPLIER_STATE[supplier] != C.STATE
    in_transit.append((arrive, po["name"], supplier, gujarat, dict(lines)))
    log(d, f"PO {po['name']} to {supplier}: ₹{po['grand_total']:,.0f} (arrives {arrive.strftime('%d %b')})")
    return po


def receive_due(d):
    for entry in [t for t in in_transit if t[0] <= d]:
        in_transit.remove(entry)
        _, po_name, supplier, gujarat, _lines = entry
        pr = erp.call("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", source_name=po_name)
        pr.update({"posting_date": iso(d), "posting_time": "10:00:00", "set_posting_time": 1})
        pr = erp.submit(pr)
        for it in pr["items"]:
            move(it["item_code"], it["qty"])
        pi = erp.call("erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice", source_name=pr["name"])
        pi.update({"posting_date": iso(d), "set_posting_time": 1, "bill_no": f"{supplier[:4].upper()}-{po_name[-5:]}",
                   "bill_date": iso(d), "due_date": iso(d + timedelta(days=30)), "payment_schedule": []})
        pi = erp.submit(pi)
        metrics["purchases"] += pi["grand_total"]
        if gujarat:
            lcv = erp.submit({"doctype": "Landed Cost Voucher", "company": C.COMPANY, "posting_date": iso(d),
                              "distribute_charges_based_on": "Qty",
                              "purchase_receipts": [{"receipt_document_type": "Purchase Receipt",
                                                     "receipt_document": pr["name"], "supplier": supplier,
                                                     "grand_total": pr["grand_total"], "posting_date": iso(d)}],
                              "items": [{"item_code": it["item_code"], "description": it["item_name"],
                                         "receipt_document_type": "Purchase Receipt", "receipt_document": pr["name"],
                                         "qty": it["qty"], "rate": it["rate"], "amount": it["amount"],
                                         "purchase_receipt_item": it["name"], "cost_center": CC}
                                        for it in pr["items"]],
                              "taxes": [{"expense_account": f"Freight and Forwarding Charges - {A}",
                                         "description": "Gujarat lorry freight", "amount": GUJARAT_FREIGHT}]})
            journal(d, "Freight paid to transporter", [(f"Freight and Forwarding Charges - {A}", GUJARAT_FREIGHT)])
            metrics["freight"] += GUJARAT_FREIGHT
            log(d, f"received {pr['name']} + freight ₹{GUJARAT_FREIGHT:,} loaded into material cost ({lcv['name']})")
        else:
            log(d, f"received {pr['name']} from {supplier}")


def purchasing(d):
    if d.weekday() == 0:  # Monday top-ups (hardware, tube, upholstery)
        for supplier, targets in WEEKLY_TOPUP.items():
            lines = {c: round(t - have(c)) for c, t in targets.items() if have(c) < 0.6 * t}
            if lines:
                place_po(supplier, lines, d, 1)
    if POLICY == "reorder":
        reorder_purchasing(d)
        return
    bulk_pending = any(t[3] for t in in_transit)
    if not bulk_pending and cash_since_bulk[0] >= BULK_TRIGGER_CASH:
        place_po("Sabarmati Laminates Wholesale", BULK_BASKET, d, GUJARAT_LEAD)
        cash_since_bulk[0] = 0
        metrics["bulk_orders"] += 1
    if have("PB-18-PRELAM") < 12 and (d - last_local_buy[0]).days >= 5 and not any(
            t[2] == "Madras Ply and Lam Traders" for t in in_transit):
        place_po("Madras Ply and Lam Traders", {"PB-18-PRELAM": 30, "BRD-UV-18": 8}, d, LOCAL_LEAD, LOCAL_PREMIUM)
        last_local_buy[0] = d
        metrics["local_emergency_buys"] += 1
        metrics["local_premium_paid"] += (30 * RATE["PB-18-PRELAM"] + 8 * RATE["BRD-UV-18"]) * (LOCAL_PREMIUM - 1)


ROP = {}
if POLICY == "reorder":
    ROP = json.loads((ROOT / "data" / "reorder_policy.json").read_text())


def on_order(code):
    return sum(t[4].get(code, 0) for t in in_transit)


last_bulk = [date(2026, 9, 1)]


def reorder_purchasing(d):
    """Joint (s, S) replenishment: when any board/laminate falls to its reorder point, order every board item
    up to its order-up-to level in ONE Gujarat load (at most one load a week), so freight isn't multiplied."""
    position = {c: have(c) + on_order(c) for c in ROP}
    triggered = [c for c, p in ROP.items() if position[c] < p["reorder_point"]]
    if triggered and (d - last_bulk[0]).days >= 7:
        lines = {c: int(p["reorder_point"] + p["order_qty"] - position[c]) for c, p in ROP.items()
                 if position[c] < p["reorder_point"] + p["order_qty"] * 0.5}
        lines = {c: q for c, q in lines.items() if q > 0}
        if lines:
            place_po("Sabarmati Laminates Wholesale", lines, d, GUJARAT_LEAD)
            last_bulk[0] = d
            metrics["bulk_orders"] += 1
    if have("PB-18-PRELAM") < ROP["PB-18-PRELAM"]["daily_usage"] * LOCAL_LEAD and \
            not any(t[4].get("PB-18-PRELAM") and (t[0] - d).days <= LOCAL_LEAD for t in in_transit) and \
            (d - last_local_buy[0]).days >= 5:
        place_po("Madras Ply and Lam Traders", {"PB-18-PRELAM": 30}, d, LOCAL_LEAD, LOCAL_PREMIUM)
        last_local_buy[0] = d
        metrics["local_emergency_buys"] += 1
        metrics["local_premium_paid"] += 30 * RATE["PB-18-PRELAM"] * (LOCAL_PREMIUM - 1)


# ------------------------------------------------------------------ accounting helpers
def journal(d, title, debits, credit_account=BANK):
    total = sum(a for _, a in debits)
    return erp.submit({"doctype": "Journal Entry", "voucher_type": "Bank Entry", "company": C.COMPANY,
                       "posting_date": iso(d), "cheque_no": title[:40], "cheque_date": iso(d), "user_remark": title,
                       "accounts": [{"account": acc, "debit_in_account_currency": amt, "cost_center": CC}
                                    for acc, amt in debits] +
                                   [{"account": credit_account, "credit_in_account_currency": total}]})


def receive_payment(si_name, d):
    si = erp.get("Sales Invoice", si_name)
    out = si["outstanding_amount"]
    if out <= 0:
        return
    inv_date = date.fromisoformat(si["posting_date"])
    disc = round(out * EARLY.get(si["customer"], 0) / 100, 2) if (d - inv_date).days <= 7 and not si["is_opening"] == "Yes" else 0
    pe = {"doctype": "Payment Entry", "payment_type": "Receive", "party_type": "Customer", "party": si["customer"],
          "company": C.COMPANY, "posting_date": iso(d), "paid_from": f"Debtors - {A}", "paid_to": BANK,
          "paid_amount": out - disc, "received_amount": out - disc, "reference_no": f"NEFT-{si_name[-5:]}",
          "reference_date": iso(d),
          "references": [{"reference_doctype": "Sales Invoice", "reference_name": si_name, "allocated_amount": out}]}
    if disc:
        pe["deductions"] = [{"account": f"Early Payment Discount Allowed - {A}", "cost_center": CC, "amount": disc}]
        metrics["early_pay_discount"] += disc
    erp.submit(pe)
    cash_since_bulk[0] += out - disc
    metrics["collections"] += out - disc
    log(d, f"received ₹{out - disc:,.0f} from {si['customer']} for {si_name}" + (f" (took ₹{disc:,.0f} early discount)" if disc else ""))


def pay_supplier(pi_name, d):
    pi = erp.get("Purchase Invoice", pi_name)
    if pi["outstanding_amount"] <= 0:
        return
    amt = pi["outstanding_amount"]
    erp.submit({"doctype": "Payment Entry", "payment_type": "Pay", "party_type": "Supplier", "party": pi["supplier"],
                "company": C.COMPANY, "posting_date": iso(d), "paid_from": BANK, "paid_to": f"Creditors - {A}",
                "paid_amount": amt, "received_amount": amt, "reference_no": f"NEFT-P-{pi_name[-5:]}",
                "reference_date": iso(d),
                "references": [{"reference_doctype": "Purchase Invoice", "reference_name": pi_name, "allocated_amount": amt}]})
    log(d, f"paid ₹{amt:,.0f} to {pi['supplier']} ({pi_name})")


# ------------------------------------------------------------------ selling
open_lines = []           # dicts: so, so_detail, customer, item, qty, due
scheduled_receipts = defaultdict(list)   # date -> [si]
pending_rework = []       # (dn name, ready date)


def new_sales_order(customer, lines, d, due):
    so = erp.submit({"doctype": "Sales Order", "customer": customer, "company": C.COMPANY,
                     "transaction_date": iso(d), "delivery_date": iso(due), "order_type": "Sales",
                     "custom_order_kind": "Standard", "set_warehouse": FG_WH,
                     "taxes_and_charges": SALES_TAX, "taxes": TAXES_SALES,
                     "items": [{"item_code": c, "qty": q, "delivery_date": iso(due), "warehouse": FG_WH}
                               for c, q in lines]})
    for it in so["items"]:
        open_lines.append({"so": so["name"], "so_detail": it["name"], "customer": customer, "item": it["item_code"],
                           "qty": it["qty"], "due": due, "ordered": d})
    metrics["orders_value"] += so["grand_total"]
    return so


SVG_DRAWING = """<svg xmlns='http://www.w3.org/2000/svg' width='520' height='360' font-family='Arial' font-size='13'>
<rect width='520' height='360' fill='white'/><text x='20' y='26' font-weight='bold'>Walk-in wardrobe — elevation (spec v1)</text>
<rect x='40' y='50' width='440' height='260' fill='none' stroke='black' stroke-width='2'/>
<line x1='186' y1='50' x2='186' y2='310' stroke='black'/><line x1='333' y1='50' x2='333' y2='310' stroke='black'/>
<line x1='40' y1='120' x2='186' y2='120' stroke='black'/><text x='60' y='100'>Loft</text>
<line x1='200' y1='90' x2='320' y2='90' stroke='black' stroke-dasharray='4'/><text x='215' y='200'>Long hanging</text>
<line x1='333' y1='140' x2='480' y2='140' stroke='black'/><line x1='333' y1='200' x2='480' y2='200' stroke='black'/>
<line x1='333' y1='255' x2='480' y2='255' stroke='black'/><text x='370' y='290'>2 drawers</text>
<text x='60' y='200'>Shelves x4</text><text x='200' y='335'>Width 2440 mm  |  Height 2400 mm  |  Depth 600 mm</text></svg>"""
# 1x1 PNG stands in for the customer's drawn signature (Signature fields store a data URL)
SIGNATURE = "data:image/png;base64," + base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cf000002020100e5276dd50000000049454e44ae426082")).decode()


def custom_order(customer, d, staff, finish, layout, block_first):
    bom = BOM[DEFAULT_BOM["WIW-CUSTOM"]]
    price = round(bom["total_cost"] * 1.8, -2)      # owner's rule: material + making cost + 80%
    base = {"doctype": "Sales Order", "customer": customer, "company": C.COMPANY, "transaction_date": iso(d),
            "delivery_date": iso(d + timedelta(days=21)), "order_type": "Sales", "set_warehouse": FG_WH,
            "custom_order_kind": "Custom", "custom_retailer_staff": staff, "custom_measured_by": "Factory measurer",
            "custom_measured_on": iso(d), "custom_site_width_mm": 2440, "custom_site_height_mm": 2400,
            "custom_site_depth_mm": 600, "custom_finish": finish, "custom_internal_layout": layout,
            "custom_spec_version": 1, "ignore_pricing_rule": 1,
            "taxes_and_charges": SALES_TAX, "taxes": TAXES_SALES,
            "items": [{"item_code": "WIW-CUSTOM", "qty": 1, "rate": price, "price_list_rate": price,
                       "delivery_date": iso(d + timedelta(days=21)), "warehouse": FG_WH}]}
    so = erp.insert(base)
    if block_first:
        try:
            erp.call("frappe.client.submit", doc=json.dumps(erp.get("Sales Order", so["name"])))
            raise SystemExit("sign-off gate did not block an unsigned custom order")
        except ERPError as e:
            msg = str(e)
            (OUT / "custom_order_gate.txt").write_text(f"{so['name']} blocked on {iso(d)}:\n{msg}\n")
            log(d, f"custom order {so['name']} BLOCKED by sign-off gate: {msg.split('Missing:')[-1].strip()}")
            metrics["custom_orders_blocked"] += 1
    r = erp.s.post(f"{BASE}/api/method/upload_file",
                   files={"file": (f"{so['name']}-drawing.svg", SVG_DRAWING.encode(), "image/svg+xml")},
                   data={"is_private": "0", "doctype": "Sales Order", "docname": so["name"],
                         "fieldname": "custom_design_drawing"})
    url = erp._check(r)["message"]["file_url"]
    so = erp.update("Sales Order", so["name"], {"custom_design_drawing": url, "custom_customer_signature": SIGNATURE,
                                                "custom_signed_on": iso(d)})
    so = erp.call("frappe.client.submit", doc=json.dumps(so))
    open_lines.append({"so": so["name"], "so_detail": so["items"][0]["name"], "customer": customer,
                       "item": "WIW-CUSTOM", "qty": 1, "due": d + timedelta(days=21), "ordered": d})
    metrics["orders_value"] += so["grand_total"]
    log(d, f"custom walk-in order {so['name']} signed and confirmed: ₹{so['grand_total']:,.0f}")


def inspect(dn, item_row, d, accepted):
    status = "Accepted" if accepted else "Rejected"
    failed = random.choice(["Handle alignment", "Finish fully dry", "Edge banding intact"]) if not accepted else None
    qi = erp.submit({"doctype": "Quality Inspection", "inspection_type": "Outgoing", "reference_type": "Delivery Note",
                     "reference_name": dn, "child_row_reference": item_row["name"], "item_code": item_row["item_code"],
                     "sample_size": item_row["qty"], "inspected_by": "Administrator", "report_date": iso(d),
                     "quality_inspection_template": QI_TEMPLATE, "manual_inspection": 1, "status": status,
                     "readings": [{"specification": p, "manual_inspection": 1,
                                   "status": "Rejected" if p == failed else "Accepted"}
                                  for p in ("Handle alignment", "Finish fully dry", "Edge banding intact",
                                            "No chipping or scratches", "Hardware complete",
                                            "Matches order specification")]})
    return qi["name"], failed


def deliver(lines, d):
    """One Delivery Note per sales order for the lines that are ready today."""
    by_so = defaultdict(list)
    for ln in lines:
        by_so[ln["so"]].append(ln)
    for so_name, lns in by_so.items():
        dn = erp.call("erpnext.selling.doctype.sales_order.sales_order.make_delivery_note", source_name=so_name)
        wanted = {ln["so_detail"]: ln["qty"] for ln in lns}
        dn["items"] = [dict(it, qty=wanted[it["so_detail"]]) for it in dn["items"] if it["so_detail"] in wanted]
        dn.update({"posting_date": iso(d), "posting_time": "18:00:00", "set_posting_time": 1})
        dn = erp.insert(dn)
        rejected = False
        for it in dn["items"]:
            if it["item_code"] not in QC_ITEMS:
                continue
            ok = random.random() > DEFECT_RATE
            qi, failed = inspect(dn["name"], it, d, ok)
            if ok:
                it["quality_inspection"] = qi
            else:
                rejected = True
                metrics["qc_rejections"] += 1
                log(d, f"QC REJECTED {it['item_code']} on {dn['name']} ({failed}) -> rework, ships tomorrow")
        for ln in lns:
            ln["qty"] = 0
        if rejected:
            erp.update("Delivery Note", dn["name"], {"items": dn["items"]})
            pending_rework.append((dn["name"], next_workday(d)))
            continue
        finalize_delivery(erp.update("Delivery Note", dn["name"], {"items": dn["items"]}), d)


def finalize_delivery(dn, d):
    dn = erp.call("frappe.client.submit", doc=json.dumps(dn))
    for it in dn["items"]:
        move(it["item_code"], -it["qty"])
        so_due = date.fromisoformat(erp.get_value("Sales Order", it["against_sales_order"], "delivery_date")["delivery_date"])
        metrics["units_delivered"] += it["qty"]
        if d > so_due:
            metrics["units_late"] += it["qty"]
        for _ in range(int(it["qty"])):
            if random.random() < DAMAGE_RATE and not it["item_code"].startswith("WIW"):
                transit_damage(dn, it, d)
    si = erp.call("erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice", source_name=dn["name"])
    si.update({"posting_date": iso(d), "set_posting_time": 1, "payment_schedule": [],
               "due_date": iso(d + timedelta(days=CREDIT[dn["customer"]]))})
    si = erp.submit(si)
    metrics["invoiced"] += si["grand_total"]
    lo, hi = PAY_DAYS[CUST_IDX[dn["customer"]]]
    scheduled_receipts[d + timedelta(days=random.randint(lo, hi))].append(si["name"])
    log(d, f"delivered {dn['name']} -> invoice {si['name']} ₹{si['grand_total']:,.0f} to {dn['customer']}")


def transit_damage(dn, it, d):
    code = it["item_code"]
    if have(code) < 1:
        bom, short = try_make(code, 1, d)
        if not bom:
            log(d, f"transit damage on {code} for {dn['customer']}: replacement pending (no stock)")
            return
    se = erp.submit({"doctype": "Stock Entry", "stock_entry_type": "Material Issue", "purpose": "Material Issue",
                     "company": C.COMPANY, "posting_date": iso(d), "posting_time": "19:00:00", "set_posting_time": 1,
                     "items": [{"item_code": code, "qty": 1, "s_warehouse": FG_WH,
                                "expense_account": f"Transit Damage Replacements - {A}", "cost_center": CC}]})
    move(code, -1)
    cost = sum(x["amount"] for x in se["items"])
    cause = random.choice(["Transit chipping", "Transit chipping", "Wet finish smudged", "Edge banding peeled"])
    erp.insert({"doctype": "Transit Damage Log", "posting_date": iso(d), "customer": dn["customer"], "item_code": code,
                "qty": 1, "cause": cause, "action": "Full replacement", "replacement_cost": cost,
                "notes": f"Against {dn['name']}; replacement issued via {se['name']}"})
    metrics["damage_cost"] += cost
    metrics["damage_units"] += 1
    log(d, f"TRANSIT DAMAGE {code} at {dn['customer']} ({cause}) -> free replacement, cost ₹{cost:,.0f}")


# ------------------------------------------------------------------ calendar
def workdays():
    d = date(2026, 10, 1)
    while d.month == 10:
        if d.weekday() != 6 and d not in HOLIDAYS:
            yield d
        d += timedelta(days=1)


def next_workday(d):
    n = d + timedelta(days=1)
    while n.weekday() == 6 or n in HOLIDAYS:
        n += timedelta(days=1)
    return n


def downtime(d, reason, util, repair=0.0, note=""):
    idle_share = max(0.0, 1 - util)
    cost = round(C.DAILY_FIXED * idle_share)
    erp.insert({"doctype": "Production Downtime", "posting_date": iso(d), "reason": reason,
                "hours_lost": round(C.SHIFT_HOURS * idle_share, 1), "workers_idle": round(C.WORKERS * idle_share),
                "idle_cost": cost, "repair_cost": repair, "notes": note})
    metrics["downtime_cost"] += cost
    metrics["downtime_days_equiv"] += idle_share
    log(d, f"DOWNTIME {reason}: {idle_share:.0%} of the day idle, ₹{cost:,.0f} of salary/fixed cost with no output")


# ------------------------------------------------------------------ main loop
def main():
    # opening receivables are collected through early October; opening payables fall due 30 days after bill
    for si in erp.list("Sales Invoice", filters={"is_opening": "Yes", "docstatus": 1}, fields=["name", "customer"]):
        lo = 2 if CUST_IDX[si["customer"]] < 4 else 18
        scheduled_receipts[date(2026, 10, 1) + timedelta(days=random.randint(lo, lo + 10))].append(si["name"])
    opening_payables = erp.list("Purchase Invoice", filters={"is_opening": "Yes", "docstatus": 1},
                                fields=["name", "bill_date"])

    for d in workdays():
        station_used.clear()
        receive_due(d)
        for day in sorted(k for k in list(scheduled_receipts) if k <= d):
            for si in scheduled_receipts.pop(day):
                receive_payment(si, d)
        for pi in [p for p in opening_payables if date.fromisoformat(p["bill_date"]) + timedelta(days=30) <= d]:
            opening_payables.remove(pi)          # due date may fall on a Sunday/holiday: pay next working day
            pay_supplier(pi["name"], d)
        purchasing(d)

        # retailer orders arrive by phone
        for _ in range(random.randint(2, 3)):
            cust = random.choices(CUSTOMERS, weights=RETAILER_WEIGHT)[0]
            lines = {}
            for _ in range(random.choice([1, 1, 2])):
                item = random.choices(list(DEMAND_MIX), weights=list(DEMAND_MIX.values()))[0]
                lines[item] = lines.get(item, 0) + random.choices([1, 2], weights=[75, 25])[0]
            new_sales_order(cust, list(lines.items()), d, d + timedelta(days=7))
        if d == date(2026, 10, 6):
            custom_order(CUSTOMERS[0], d, "Padi floor staff", "Marble white gloss doors",
                         "Loft; 4 shelves left; long hanging centre; 2 drawers right", block_first=True)
        if d == date(2026, 10, 21):
            custom_order(CUSTOMERS[1], d, "Pallikaranai floor staff", "Walnut matte",
                         "Short hanging x2; 5 shelves; 3 drawers", block_first=False)

        # rework from yesterday's QC failures ships first
        for dn_name, ready in [r for r in pending_rework if r[1] <= d]:
            pending_rework.remove((dn_name, ready))
            dn = erp.get("Delivery Note", dn_name)
            for it in dn["items"]:
                if it["item_code"] in QC_ITEMS and not it.get("quality_inspection"):
                    it["quality_inspection"], _ = inspect(dn_name, it, d, True)
            dn.update({"posting_date": iso(d), "set_posting_time": 1})
            finalize_delivery(erp.update("Delivery Note", dn_name, {"items": dn["items"], "posting_date": iso(d),
                                                                    "set_posting_time": 1}), d)

        # production: nearest deadline first (as-is behaviour)
        blocked_by_material = set()
        for ln in sorted([l for l in open_lines if l["qty"] > 0], key=lambda l: l["due"]):
            ready_qty = have(ln["item"])
            need = ln["qty"] - ready_qty
            if need > 0:
                bom, short = try_make(ln["item"], need, d)
                if not bom and "capacity" not in short:
                    blocked_by_material.update(short)
        ready = []
        for ln in sorted([l for l in open_lines if l["qty"] > 0], key=lambda l: l["due"]):
            if have(ln["item"]) >= ln["qty"]:
                stock[(ln["item"], FG_WH)] -= ln["qty"]          # reserve for this delivery
                ready.append(dict(ln))
        for ln in ready:
            stock[(ln["item"], FG_WH)] += ln["qty"]
        if ready:
            deliver(ready, d)
            ids = {r["so_detail"] for r in ready}
            for ln in open_lines:
                if ln["so_detail"] in ids:
                    ln["qty"] = 0

        # Saturday: turn offcuts into frames, clocks and scooters
        if d.weekday() == 5:
            for code, n in (("SP-PHOTO-FRAME", 12), ("SP-WALL-CLOCK", 6), ("SP-KIDS-SCOOTER", 3)):
                b = DEFAULT_BOM[code]
                if not bom_shortages(b, n) and stations_free(b, n, d):
                    manufacture(b, n, d)
                    metrics["scrap_products_made"] += n
            lines = [(c, int(have(c))) for c in ("SP-PHOTO-FRAME", "SP-WALL-CLOCK", "SP-KIDS-SCOOTER") if have(c) >= 1]
            if lines:
                so = new_sales_order(CUSTOMERS[2], lines, d, d)
                deliver([l for l in open_lines if l["so"] == so["name"]], d)

        # downtime accounting
        util = station_used["Assembly Bay"] / capacity("Assembly Bay", d)
        boards = {c for c in blocked_by_material if c in RATE and GROUP[c] in ("Boards", "Laminates")}
        if d == CNC_BREAKDOWN:
            downtime(d, "Machine breakdown", max(util, 0.5), repair=10_000,
                     note="CNC router down; repair technician visit. CNC-dependent jobs (mandirs, wardrobes) held.")
            journal(d, "CNC repair visit", [(f"Machine Repairs - {A}", 10_000)])
            metrics["repairs"] += 10_000
        elif boards and util < 0.6:
            downtime(d, "No sheets in stock", util, note="Short: " + ", ".join(sorted(boards)))

    month_end(date(2026, 10, 31))


def month_end(d):
    journal(d, "October salaries", [(f"Salary - {A}", C.MONTHLY_SALARIES)])
    journal(d, "October factory rent", [(f"Office Rent - {A}", C.MONTHLY_RENT)])
    journal(d, "October electricity", [(f"Utility Expenses - {A}", C.MONTHLY_POWER)])
    journal(d, "October other fixed costs", [(f"Miscellaneous Expenses - {A}", C.MONTHLY_OTHER_FIXED)])
    # physical count of hardware: book qty vs counted qty
    loss = {"HW-HINGE-SC": 0.05, "HW-MINIFIX": 0.08, "HW-SCREW-BOX": 0.10, "HW-HANDLE": 0.03, "HW-LOCK": 0.04}
    rows, value = [], 0.0
    for code, pct in loss.items():
        book = have(code)
        counted = int(book * (1 - pct))
        rate = erp.get_value("Bin", {"item_code": code, "warehouse": HW_WH}, "valuation_rate")["valuation_rate"]
        rows.append({"item_code": code, "warehouse": HW_WH, "qty": counted, "valuation_rate": rate})
        value += (book - counted) * rate
        move(code, counted - book)
    sr = erp.submit({"doctype": "Stock Reconciliation", "company": C.COMPANY, "purpose": "Stock Reconciliation",
                     "posting_date": iso(d), "posting_time": "20:00:00", "set_posting_time": 1,
                     "expense_account": f"Stock Adjustment - {A}", "items": rows})
    metrics["hardware_shrinkage"] = value
    log(d, f"month-end hardware count {sr['name']}: ₹{value:,.0f} of hardware missing vs book")
    metrics["policy"] = POLICY
    (OUT / f"simulation_log_{POLICY}.txt").write_text("\n".join(log_lines) + "\n")
    (OUT / f"simulation_metrics_{POLICY}.json").write_text(json.dumps(metrics, indent=2, default=float))
    print(json.dumps(metrics, indent=2, default=float))


if __name__ == "__main__":
    main()
