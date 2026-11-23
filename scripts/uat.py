"""User-acceptance test cases, executed against the configured ERPNext site (posting date 3 Nov 2026).

Each case runs its steps through the API, compares the actual outcome with the expected one, and is written
to docs/08-uat-test-cases.md as PASS/FAIL with evidence (document numbers, amounts).
Run after simulate.py, planning.py, approvals.py and the intake setup.
"""
import json
import subprocess
import traceback
from pathlib import Path

import catalog as C
from erp import BASE, ERP, ERPError, sql

A = C.ABBR
ROOT = Path(__file__).resolve().parent.parent
D = "2026-11-03"
erp = ERP()
RM, HW, FG, SCRAP = (f"{w} - {A}" for w in ("Raw Material Store", "Hardware Store", "Finished Goods Store", "Scrap Yard"))
results = []


def case(cid, process, steps, expected):
    def wrap(fn):
        try:
            ok, actual = fn()
        except Exception as e:                       # a crash is a failed test, with the reason recorded
            ok, actual = False, f"error: {str(e)[:200]}"
            traceback.print_exc()
        results.append((cid, process, steps, expected, actual, "PASS" if ok else "FAIL"))
        print(f"{cid} {'PASS' if ok else 'FAIL'}  {actual}")
        return fn
    return wrap


def taxes(kind, name):
    return erp.call("erpnext.controllers.accounts_controller.get_taxes_and_charges",
                    master_doctype=f"{kind} Taxes and Charges Template", master_name=name)


def wf(doc, action):
    return erp.call("frappe.model.workflow.apply_workflow", doc=json.dumps(doc), action=action)


def purchase(supplier, item, qty, rate, tpl):
    po = erp.insert({"doctype": "Purchase Order", "supplier": supplier, "company": C.COMPANY, "transaction_date": D,
                     "schedule_date": D, "taxes_and_charges": tpl, "taxes": taxes("Purchase", tpl),
                     "items": [{"item_code": item, "qty": qty, "rate": rate, "schedule_date": D,
                                "warehouse": HW if item.startswith("HW-") else RM}]})
    po = wf(po, "Approve")
    pr = erp.call("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", source_name=po["name"])
    pr.update({"posting_date": D, "set_posting_time": 1})
    pr = erp.submit(pr)
    pi = erp.call("erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice", source_name=pr["name"])
    pi.update({"posting_date": D, "set_posting_time": 1, "bill_no": f"UAT-{pr['name'][-5:]}", "bill_date": D,
               "payment_schedule": [], "due_date": "2026-12-03"})
    return po, pr, erp.submit(pi)


def bin_qty(item, wh):
    r = erp.list("Bin", filters={"item_code": item, "warehouse": wh}, fields=["actual_qty", "valuation_rate"])
    return (float(r[0]["actual_qty"]), float(r[0]["valuation_rate"])) if r else (0.0, 0.0)


def manufacture(bom_name, qty):
    b = erp.get("BOM", bom_name)
    defaults = {}
    items = []
    for i in b["items"]:
        code = i["item_code"]
        defaults[code] = erp.get("Item", code)["item_defaults"][0]["default_warehouse"]
        items.append({"item_code": code, "qty": i["stock_qty"] * qty, "s_warehouse": defaults[code]})
    items.append({"item_code": b["item"], "qty": qty, "t_warehouse": FG, "is_finished_item": 1})
    for s in b["secondary_items"]:
        items.append({"item_code": s["item_code"], "qty": s["stock_qty"] * qty, "t_warehouse": SCRAP,
                      "secondary_item_type": "Scrap", "valuation_type": "Valuation Rate"})
    comp = {}
    for op in b["operations"]:
        for c in erp.get("Workstation", op["workstation"])["workstation_costs"]:
            comp[c["operating_component"]] = comp.get(c["operating_component"], 0) + c["operating_cost"] * op["time_in_mins"] / 60 * qty
    return erp.submit({"doctype": "Stock Entry", "stock_entry_type": "Manufacture", "purpose": "Manufacture",
                       "company": C.COMPANY, "from_bom": 1, "bom_no": bom_name, "fg_completed_qty": qty,
                       "posting_date": D, "set_posting_time": 1, "items": items,
                       "additional_costs": [{"expense_account": f"Expenses Included In Valuation - {A}",
                                             "description": f"Operating cost - {k}", "amount": round(v, 2),
                                             "has_operating_cost": 1, "operating_component": k} for k, v in comp.items()]}), b


ctx = {}


@case("UAT-01", "Procure-to-pay (local)", "PO 10 pre-lam boards from a Chennai supplier → receipt → bill",
      "Stock +10; bill carries CGST 9% + SGST 9%; payable = bill total")
def _():
    before, _ = bin_qty("PB-18-PRELAM", RM)
    po, pr, pi = purchase("Madras Ply and Lam Traders", "PB-18-PRELAM", 10, 1450, f"GST 18% In-State - {A}")
    after, _ = bin_qty("PB-18-PRELAM", RM)
    heads = sorted((t["account_head"].split(" - ")[0], t["rate"]) for t in pi["taxes"])
    ok = after - before == 10 and heads == [("Input CGST", 9), ("Input SGST", 9)] and pi["outstanding_amount"] == pi["grand_total"]
    return ok, f"{po['name']} → {pr['name']} → {pi['name']}; stock {before:.0f}→{after:.0f}; taxes {heads}; payable ₹{pi['outstanding_amount']:,.0f}"


@case("UAT-02", "Procure-to-pay (inter-state)", "PO 5 UV boards from the Gujarat wholesaler → receipt → bill",
      "Bill carries IGST 18% only")
def _():
    po, pr, pi = purchase("Sabarmati Laminates Wholesale", "BRD-UV-18", 5, 4300, f"IGST 18% Inter-State - {A}")
    ctx["igst_pr"] = pr
    heads = [(t["account_head"].split(" - ")[0], t["rate"]) for t in pi["taxes"]]
    return heads == [("Input IGST", 18)], f"{pi['name']}: taxes {heads}"


@case("UAT-03", "Landed cost", "Landed Cost Voucher: ₹1,000 lorry freight on the UAT-02 receipt",
      "Valuation of each of the 5 boards rises by ₹200")
def _():
    pr = ctx["igst_pr"]
    it = pr["items"][0]
    erp.submit({"doctype": "Landed Cost Voucher", "company": C.COMPANY, "posting_date": D,
                "distribute_charges_based_on": "Qty",
                "purchase_receipts": [{"receipt_document_type": "Purchase Receipt", "receipt_document": pr["name"],
                                       "supplier": pr["supplier"], "grand_total": pr["grand_total"], "posting_date": D}],
                "items": [{"item_code": it["item_code"], "description": it["item_name"], "qty": it["qty"],
                           "rate": it["rate"], "amount": it["amount"], "receipt_document_type": "Purchase Receipt",
                           "receipt_document": pr["name"], "purchase_receipt_item": it["name"], "cost_center": f"Main - {A}"}],
                "taxes": [{"expense_account": f"Freight and Forwarding Charges - {A}", "description": "UAT freight", "amount": 1000}]})
    rate = float(sql(f"SELECT incoming_rate v FROM `tabStock Ledger Entry` WHERE voucher_no = '{pr['name']}' "
                     f"AND is_cancelled = 0 ORDER BY creation DESC LIMIT 1")[0]["v"])
    return abs(rate - 4500) < 0.01, f"incoming rate per board after freight: ₹{rate:,.2f} (was ₹4,300.00)"


@case("UAT-04", "MRP", "Production Plan over November's open orders",
      "Requirements netted against stock; material requests raised only for shortfalls")
def _():
    plan = json.loads((ROOT / "out" / "production_plan.json").read_text())
    short = {k: v for k, v in plan["materials"].items() if v["to_request"] > 0}
    covered = [k for k, v in plan["materials"].items() if v["to_request"] == 0 and v["required"] <= v["in_stock"]]
    mrs = [m for m in plan["material_requests"] if erp.exists("Material Request", m)]
    ok = bool(mrs) and all(v["to_request"] <= v["required"] for v in short.values()) and len(covered) > 0
    return ok, f"{plan['plan']}: {len(short)} items short → {len(mrs)} material requests; {len(covered)} items fully covered by stock"


@case("UAT-05", "Manufacture + costing", "Manufacture 1 two-door wardrobe from its default BOM",
      "Finished-good value = materials + machine-hour cost − scrap, within 3% of the BOM cost")
def _():
    se, b = manufacture("BOM-WRD-2D-001", 1)
    fg = next(i for i in se["items"] if i.get("is_finished_item"))
    ctx["se"] = se
    diff = abs(fg["valuation_rate"] - b["total_cost"]) / b["total_cost"]
    return diff < 0.03, f"{se['name']}: wardrobe valued ₹{fg['valuation_rate']:,.0f} vs BOM ₹{b['total_cost']:,.0f} ({100 * diff:.1f}% apart, from moving-average material prices)"


@case("UAT-06", "Scrap recovery", "Same manufacture entry", "9 sq ft of offcuts land in the Scrap Yard at their scrap value")
def _():
    sc = next(i for i in ctx["se"]["items"] if i.get("secondary_item_type") == "Scrap")
    return sc["t_warehouse"] == SCRAP and sc["qty"] == 9, f"{sc['qty']:.0f} {sc['item_code']} → {sc['t_warehouse']} @ ₹{sc['valuation_rate']:.0f}"


@case("UAT-07", "Retailer pricing", "Sales Order for Mega Home Stores: 1 two-door wardrobe",
      "Retailer's 10% standing discount applied automatically: ₹31,000 → ₹27,900")
def _():
    so = erp.submit({"doctype": "Sales Order", "customer": "Mega Home Stores - Padi", "company": C.COMPANY,
                     "transaction_date": D, "delivery_date": "2026-11-10", "custom_order_kind": "Standard",
                     "set_warehouse": FG, "taxes_and_charges": f"GST 18% In-State - {A}",
                     "taxes": taxes("Sales", f"GST 18% In-State - {A}"),
                     "items": [{"item_code": "WRD-2D", "qty": 1, "delivery_date": "2026-11-10", "warehouse": FG}]})
    ctx["so"] = so
    it = so["items"][0]
    return it["discount_percentage"] == 10 and it["rate"] == 27900, f"{so['name']}: list ₹{it['price_list_rate']:,.0f}, discount {it['discount_percentage']}%, rate ₹{it['rate']:,.0f}"


@case("UAT-08", "Pre-dispatch QC gate", "Delivery Note for UAT-07 submitted without a quality inspection",
      "Submission refused")
def _():
    dn = erp.call("erpnext.selling.doctype.sales_order.sales_order.make_delivery_note", source_name=ctx["so"]["name"])
    dn.update({"posting_date": D, "set_posting_time": 1})
    dn = erp.insert(dn)
    ctx["dn"] = dn
    try:
        erp.call("frappe.client.submit", doc=json.dumps(dn))
        return False, "delivery submitted without QC"
    except ERPError as e:
        return "Quality Inspection" in str(e), f"refused: {str(e).split(': ', 1)[-1][:150]}"


@case("UAT-09", "Order-to-cash", "Record an accepted 6-point inspection, submit the delivery, invoice it",
      "Delivery submits; invoice carries CGST 9% + SGST 9%; due in 14 days")
def _():
    dn = erp.get("Delivery Note", ctx["dn"]["name"])
    it = dn["items"][0]
    qi = erp.submit({"doctype": "Quality Inspection", "inspection_type": "Outgoing", "reference_type": "Delivery Note",
                     "reference_name": dn["name"], "child_row_reference": it["name"], "item_code": it["item_code"],
                     "sample_size": 1, "inspected_by": "Administrator", "report_date": D,
                     "quality_inspection_template": "Pre-Dispatch Furniture Check", "manual_inspection": 1,
                     "status": "Accepted"})
    it["quality_inspection"] = qi["name"]
    dn = erp.call("frappe.client.submit", doc=json.dumps(erp.update("Delivery Note", dn["name"], {"items": dn["items"]})))
    si = erp.call("erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice", source_name=dn["name"])
    si.update({"posting_date": D, "set_posting_time": 1, "payment_schedule": [], "due_date": "2026-11-17"})
    si = erp.submit(si)
    ctx["si"] = si
    heads = sorted((t["account_head"].split(" - ")[0], t["rate"]) for t in si["taxes"])
    ok = dn["docstatus"] == 1 and heads == [("Output CGST", 9), ("Output SGST", 9)] and si["due_date"] == "2026-11-17"
    return ok, f"{qi['name']} accepted → {dn['name']} → {si['name']} ₹{si['grand_total']:,.0f} due {si['due_date']}"


@case("UAT-10", "Early-payment discount", "Retailer pays the UAT-09 invoice within 7 days",
      "2% discount posted to 'Early Payment Discount Allowed'; invoice fully settled")
def _():
    si = ctx["si"]
    disc = round(si["outstanding_amount"] * 0.02, 2)
    pe = erp.submit({"doctype": "Payment Entry", "payment_type": "Receive", "party_type": "Customer",
                     "party": si["customer"], "company": C.COMPANY, "posting_date": "2026-11-06",
                     "paid_from": f"Debtors - {A}", "paid_to": f"HDFC Current Account - {A}",
                     "paid_amount": si["outstanding_amount"] - disc, "received_amount": si["outstanding_amount"] - disc,
                     "reference_no": "UAT-NEFT", "reference_date": "2026-11-06",
                     "references": [{"reference_doctype": "Sales Invoice", "reference_name": si["name"],
                                     "allocated_amount": si["outstanding_amount"]}],
                     "deductions": [{"account": f"Early Payment Discount Allowed - {A}", "cost_center": f"Main - {A}", "amount": disc}]})
    gl = float(sql(f"SELECT SUM(debit) v FROM `tabGL Entry` WHERE voucher_no = '{pe['name']}' "
                   f"AND account = 'Early Payment Discount Allowed - {A}' AND is_cancelled = 0")[0]["v"])
    left = erp.get("Sales Invoice", si["name"])["outstanding_amount"]
    return abs(gl - disc) < 0.01 and left == 0, f"{pe['name']}: discount ₹{gl:,.2f} posted; outstanding now ₹{left:,.0f}"


def custom_so(signed):
    signed_so = erp.list("Sales Order", filters={"custom_order_kind": "Custom", "docstatus": 1},
                         fields=["name", "custom_design_drawing", "custom_customer_signature"], limit=1)[0]
    doc = {"doctype": "Sales Order", "customer": "Kanchi Furniture Mart", "company": C.COMPANY, "transaction_date": D,
           "delivery_date": "2026-11-24", "custom_order_kind": "Custom", "custom_retailer_staff": "Kanchi floor staff",
           "custom_site_width_mm": 2440, "custom_site_height_mm": 2400, "custom_site_depth_mm": 600,
           "custom_finish": "Walnut matte", "ignore_pricing_rule": 1, "set_warehouse": FG,
           "items": [{"item_code": "WIW-CUSTOM", "qty": 1, "rate": 109300, "delivery_date": "2026-11-24"}]}
    if signed:
        doc.update({"custom_internal_layout": "4 shelves; long hanging; 2 drawers", "custom_spec_version": 1,
                    "custom_design_drawing": signed_so["custom_design_drawing"],
                    "custom_customer_signature": signed_so["custom_customer_signature"], "custom_signed_on": D})
    return erp.insert(doc)


@case("UAT-11", "Custom-order sign-off gate", "Confirm a walk-in wardrobe order with no layout, drawing or signature",
      "Confirmation refused, listing what is missing")
def _():
    so = custom_so(False)
    try:
        erp.call("frappe.client.submit", doc=json.dumps(so))
        return False, "unsigned custom order was confirmed"
    except ERPError as e:
        erp.s.delete(f"{BASE}/api/resource/Sales Order/{so['name']}")
        return "Missing" in str(e), str(e).split("Missing:")[-1].strip()[:150]


@case("UAT-12", "Custom-order sign-off gate", "Same order with full specification, drawing and customer signature",
      "Order confirmed")
def _():
    so = custom_so(True)
    so = erp.call("frappe.client.submit", doc=json.dumps(so))
    return so["docstatus"] == 1, f"{so['name']} confirmed with signed spec v{so['custom_spec_version']}"


@case("UAT-13", "Transit damage", "Log a damaged delivery and issue a free replacement",
      "Damage logged; replacement cost charged to 'Transit Damage Replacements'")
def _():
    item = "OFT-5FT"
    purchase("Parrys Hardware Mart", "HW-SCREW-BOX", 5, 180, f"GST 18% In-State - {A}")   # MRP flagged screws short
    manufacture("BOM-OFT-5FT-001", 1)            # the replacement unit is built, then given away
    se = erp.submit({"doctype": "Stock Entry", "stock_entry_type": "Material Issue", "purpose": "Material Issue",
                     "company": C.COMPANY, "posting_date": D, "set_posting_time": 1,
                     "items": [{"item_code": item, "qty": 1, "s_warehouse": FG,
                                "expense_account": f"Transit Damage Replacements - {A}", "cost_center": f"Main - {A}"}]})
    cost = se["items"][0]["amount"]
    log = erp.insert({"doctype": "Transit Damage Log", "posting_date": D, "customer": "Kovai Home Centre",
                      "item_code": item, "qty": 1, "cause": "Transit chipping", "action": "Full replacement",
                      "replacement_cost": cost, "notes": f"UAT; replacement via {se['name']}"})
    gl = float(sql(f"SELECT SUM(debit) v FROM `tabGL Entry` WHERE voucher_no = '{se['name']}' "
                   f"AND account = 'Transit Damage Replacements - {A}' AND is_cancelled = 0")[0]["v"])
    return abs(gl - cost) < 0.01, f"{log['name']} + {se['name']}: ₹{gl:,.0f} charged for {item}"


@case("UAT-14", "Roles", "Check what the Retail Floor Sales and Accountant profiles can do",
      "Floor sales cannot see purchase bills; the accountant cannot approve purchase orders")
def _():
    sales_pi = sql("SELECT COUNT(*) v FROM `tabDocPerm` WHERE parent = 'Purchase Invoice' AND role = 'Sales User'")[0]["v"]
    sales_pi += sql("SELECT COUNT(*) v FROM `tabCustom DocPerm` WHERE parent = 'Purchase Invoice' AND role = 'Sales User'")[0]["v"]
    acct = {r["role"] for r in erp.get("Role Profile", "Factory Accountant")["roles"]}
    approvers = {t["allowed"] for t in erp.get("Workflow", "Purchase Order Approval")["transitions"]
                 if t["state"] == "Pending Approval" and t["action"] == "Approve"}
    ok = int(float(sales_pi)) == 0 and not (acct & approvers)
    return ok, f"Sales User perms on Purchase Invoice: {int(float(sales_pi))}; accountant roles ∩ approver roles: {sorted(acct & approvers) or 'none'}"


@case("UAT-15", "Order-intake integration", "Run the intake service's integration test suite against this site",
      "Orders from floor staff create confirmed Sales Orders exactly once, including through an outage")
def _():
    d = ROOT / "integration" / "order_intake"
    r = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-m", "pytest", "-q", "test_intake.py"],
                       cwd=d, capture_output=True, text=True)
    tail = [ln for ln in r.stdout.splitlines() if ln.strip()][-1]
    return r.returncode == 0, f"pytest: {tail}"


@case("UAT-16", "Purchase approval", "PO for 100 pre-lam boards (≈ ₹1.7 L) through the approval workflow",
      "Stays unsubmitted in 'Pending Approval' until the owner approves; then submitted")
def _():
    po = erp.insert({"doctype": "Purchase Order", "supplier": "Morbi Board Industries", "company": C.COMPANY,
                     "transaction_date": D, "schedule_date": "2026-11-15",
                     "taxes_and_charges": f"IGST 18% Inter-State - {A}", "taxes": taxes("Purchase", f"IGST 18% Inter-State - {A}"),
                     "items": [{"item_code": "PB-18-PRELAM", "qty": 100, "rate": 1450, "warehouse": RM,
                                "schedule_date": "2026-11-15"}]})
    try:
        wf(po, "Approve")
        return False, "large PO approved straight from Draft"
    except ERPError:
        pass
    po = wf(erp.get("Purchase Order", po["name"]), "Submit for Approval")
    pending = (po["workflow_state"], po["docstatus"])
    po = wf(po, "Approve")
    return pending == ("Pending Approval", 0) and po["docstatus"] == 1, \
        f"{po['name']} ₹{po['grand_total']:,.0f}: direct approval refused → {pending[0]} (draft) → {po['workflow_state']} (submitted)"


@case("UAT-17", "Reporting", "Daily Sales and Profit report for October vs the general ledger",
      "Report's net sales equal October sales income in the ledger")
def _():
    rows = erp.call("frappe.desk.query_report.run", report_name="Daily Sales and Profit",
                    filters=json.dumps({"from_date": "2026-10-01", "to_date": "2026-10-31"}))["result"]
    rep = sum(float(r[3] if isinstance(r, list) else list(r.values())[3]) for r in rows)
    gl = float(sql("SELECT SUM(g.credit - g.debit) v FROM `tabGL Entry` g JOIN `tabAccount` a ON a.name = g.account "
                   "WHERE a.root_type = 'Income' AND g.is_cancelled = 0 AND g.voucher_type = 'Sales Invoice' "
                   "AND g.posting_date BETWEEN '2026-10-01' AND '2026-10-31'")[0]["v"])
    return abs(rep - gl) < 1, f"report ₹{rep:,.0f} vs ledger ₹{gl:,.0f}"


@case("UAT-18", "Migration", "Opening balances reconciliation (reconcile.py)", "All checks PASS")
def _():
    text = (ROOT / "out" / "reconciliation_report.md").read_text()
    return "**Overall: PASS**" in text, " · ".join(ln[3:] for ln in text.splitlines() if ln.startswith("## "))


passed = sum(r[-1] == "PASS" for r in results)
lines = ["# 8. User-acceptance test cases", "",
         f"Executed by `scripts/uat.py` against the configured site. **{passed}/{len(results)} PASS.**",
         "Evidence column quotes the documents the test created or checked.", "",
         "| ID | Process | Steps | Expected | Actual / evidence | Result |", "|---|---|---|---|---|---|"]
lines += ["| " + " | ".join(str(x).replace("|", "/") for x in r) + " |" for r in results]
(ROOT / "docs" / "08-uat-test-cases.md").write_text("\n".join(lines) + "\n")
print(f"{passed}/{len(results)} PASS")
