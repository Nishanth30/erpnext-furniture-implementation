"""Configure ERPNext for the furniture factory (idempotent; safe to re-run).

Order matters: setup wizard -> accounts/tax -> UOMs/groups -> warehouses -> parties ->
workstations/operations -> items -> BOMs -> quality -> customisations (custom order spec,
damage log, downtime log).
"""
import json
import subprocess

import catalog as C
from erp import ERP, ERPError

A = C.ABBR
erp = None


def log(msg):
    print(f"  - {msg}")


# ------------------------------------------------------------------ 1. setup wizard
def setup_wizard():
    if erp.list("Company", fields=["name"]):
        log("setup wizard already complete")
        return
    args = {
        "language": "English", "country": "India", "timezone": "Asia/Kolkata", "currency": "INR",
        "full_name": "Implementation Admin", "email": "admin@kfw.test",
        "company_name": C.COMPANY, "company_abbr": A, "chart_of_accounts": "Standard",
        "fy_start_date": "2026-04-01", "fy_end_date": "2027-03-31", "setup_demo": 0,
    }
    erp.call("frappe.desk.page.setup_wizard.setup_wizard.setup_complete", args=json.dumps(args))
    log("setup wizard complete")


# ------------------------------------------------------------------ 2. accounts + GST
def accounts_and_tax():
    parent = f"Duties and Taxes - {A}"
    for acc in ["Output CGST", "Output SGST", "Output IGST", "Input CGST", "Input SGST", "Input IGST"]:
        erp.ensure({"doctype": "Account", "name": f"{acc} - {A}", "account_name": acc,
                    "parent_account": parent, "company": C.COMPANY, "account_type": "Tax", "is_group": 0})
    erp.ensure({"doctype": "Account", "name": f"HDFC Current Account - {A}", "account_name": "HDFC Current Account",
                "parent_account": f"Bank Accounts - {A}", "company": C.COMPANY, "account_type": "Bank", "is_group": 0})
    for acc in ["Transit Damage Replacements", "Production Downtime", "Early Payment Discount Allowed",
                "Machine Repairs"]:
        erp.ensure({"doctype": "Account", "name": f"{acc} - {A}", "account_name": acc,
                    "parent_account": f"Indirect Expenses - {A}", "company": C.COMPANY, "is_group": 0})

    def rows(*pairs):
        return [{"charge_type": "On Net Total", "account_head": f"{h} - {A}", "description": h, "rate": r}
                for h, r in pairs]

    sales = f"GST 18% In-State - {A}"
    if not erp.exists("Sales Taxes and Charges Template", sales):
        erp.insert({"doctype": "Sales Taxes and Charges Template", "title": "GST 18% In-State", "company": C.COMPANY,
                    "is_default": 1, "taxes": rows(("Output CGST", 9), ("Output SGST", 9))})
    for title, pairs in [("GST 18% In-State", (("Input CGST", 9), ("Input SGST", 9))),
                         ("IGST 18% Inter-State", (("Input IGST", 18),))]:
        if not erp.exists("Purchase Taxes and Charges Template", f"{title} - {A}"):
            erp.insert({"doctype": "Purchase Taxes and Charges Template", "title": title, "company": C.COMPANY,
                        "taxes": [dict(r, category="Total", add_deduct_tax="Add") for r in rows(*pairs)]})
    log("GST accounts and templates")


# ------------------------------------------------------------------ 3. UOMs, groups, settings
UOM_OVERRIDE = {"Boards": "Sheet", "Laminates": "Sheet", "UPH-FOAM-40D": "Sheet", "HW-SCREW-BOX": "Box"}


def uom_for(code, group, default):
    return UOM_OVERRIDE.get(code) or UOM_OVERRIDE.get(group) or default


def basics():
    for uom in ["Sheet", "Box"]:
        if not erp.exists("UOM", uom):
            erp.insert({"doctype": "UOM", "uom_name": uom, "must_be_whole_number": 0})
        else:
            erp.update("UOM", uom, {"must_be_whole_number": 0})
    for name, parent in [("Furniture Raw Materials", "All Item Groups"), ("Furniture Products", "All Item Groups")]:
        erp.ensure({"doctype": "Item Group", "name": name, "item_group_name": name,
                    "parent_item_group": parent, "is_group": 1})
    raw_groups = {g for _, _, g, _, _ in C.RAW_MATERIALS} | {"Scrap", "Sub Assemblies"}
    fg_groups = {g for _, _, g, _ in C.FINISHED_GOODS} | {"Scrap Products"}
    for g in raw_groups:
        if not erp.exists("Item Group", g):
            erp.insert({"doctype": "Item Group", "item_group_name": g, "parent_item_group": "Furniture Raw Materials"})
    for g in fg_groups:
        if not erp.exists("Item Group", g):
            erp.insert({"doctype": "Item Group", "item_group_name": g, "parent_item_group": "Furniture Products"})
    erp.ensure({"doctype": "Customer Group", "name": "Retailers", "customer_group_name": "Retailers",
                "parent_customer_group": "All Customer Groups"})
    for g in ["Board and Laminate Suppliers", "Hardware Suppliers"]:
        erp.ensure({"doctype": "Supplier Group", "name": g, "supplier_group_name": g,
                    "parent_supplier_group": "All Supplier Groups"})
    erp.ensure({"doctype": "Territory", "name": "Tamil Nadu", "territory_name": "Tamil Nadu",
                "parent_territory": "India"}) if erp.exists("Territory", "India") else None
    erp.update("Stock Settings", "Stock Settings", {"valuation_method": "Moving Average",
                                                     "allow_negative_stock": 0, "auto_indent": 1})
    erp.update("Selling Settings", "Selling Settings", {"so_required": "No", "dn_required": "No"})
    erp.update("Buying Settings", "Buying Settings", {"po_required": "No", "pr_required": "No"})
    log("UOMs, groups, settings")


# ------------------------------------------------------------------ 4. warehouses
def wh(name):
    return f"{name} - {A}"


def warehouses():
    for w in C.WAREHOUSES:
        if not erp.exists("Warehouse", wh(w)):
            erp.insert({"doctype": "Warehouse", "warehouse_name": w, "company": C.COMPANY,
                        "parent_warehouse": f"All Warehouses - {A}"})
    log("warehouses")


# ------------------------------------------------------------------ 5. payment terms + parties
def parties():
    for days, disc, valid in [(30, 0, 0), (14, 2.0, 7), (14, 1.5, 7), (14, 1.0, 7)]:
        term = f"Net {days}" + (f" - {disc}% if paid in {valid} days" if disc else "")
        if not erp.exists("Payment Term", term):
            t = {"doctype": "Payment Term", "payment_term_name": term, "invoice_portion": 100,
                 "due_date_based_on": "Day(s) after invoice date", "credit_days": days}
            if disc:
                t.update({"discount_type": "Percentage", "discount": disc,
                          "discount_validity_based_on": "Day(s) after invoice date", "discount_validity": valid})
            erp.insert(t)
        if not erp.exists("Payment Terms Template", term):
            erp.insert({"doctype": "Payment Terms Template", "template_name": term,
                        "terms": [{"payment_term": term, "invoice_portion": 100,
                                   "due_date_based_on": "Day(s) after invoice date", "credit_days": days}]})
    for name, city, state, days, kind in C.SUPPLIERS:
        if not erp.exists("Supplier", name):
            group = "Hardware Suppliers" if kind in ("hardware", "MS tube", "foam, fabric") else "Board and Laminate Suppliers"
            erp.insert({"doctype": "Supplier", "supplier_name": name, "supplier_group": group,
                        "payment_terms": "Net 30", "country": "India"})
    for name, city, days, disc, early, note in C.CUSTOMERS:
        if not erp.exists("Customer", name):
            erp.insert({"doctype": "Customer", "customer_name": name, "customer_group": "Retailers",
                        "customer_type": "Company", "territory": "Tamil Nadu" if erp.exists("Territory", "Tamil Nadu") else "All Territories",
                        "payment_terms": f"Net {days} - {early}% if paid in 7 days"})
        rule = f"Retailer discount {name}"[:140]
        if not erp.list("Pricing Rule", filters={"title": rule}, fields=["name"]):
            erp.insert({"doctype": "Pricing Rule", "title": rule, "apply_on": "Item Group",
                        "item_groups": [{"item_group": "Furniture Products"}], "applicable_for": "Customer",
                        "customer": name, "selling": 1, "rate_or_discount": "Discount Percentage",
                        "discount_percentage": disc, "company": C.COMPANY, "priority": "1", "valid_from": "2026-04-01"})
    log("payment terms, suppliers, retailers, retailer pricing rules")


# ------------------------------------------------------------------ 6. workstations + operations
def production_setup():
    for name, crew, pshare, fshare in C.WORKSTATIONS:
        r = C.station_rates(crew, pshare, fshare)
        costs = {"workstation_costs": [
            {"operating_component": "Wages", "operating_cost": r["hour_rate_labour"]},
            {"operating_component": "Electricity", "operating_cost": r["hour_rate_electricity"]},
            {"operating_component": "Rent", "operating_cost": r["hour_rate_rent"]}]}
        if erp.exists("Workstation", name):
            erp.update("Workstation", name, costs)
        else:
            erp.insert(dict({"doctype": "Workstation", "workstation_name": name,
                             "production_capacity": 1}, **costs))
    for op, ws in C.OPERATIONS.items():
        if not erp.exists("Operation", op):
            erp.insert({"doctype": "Operation", "name": op, "workstation": ws})
    log("workstations (hour rates = labour + power + rent) and operations")


# ------------------------------------------------------------------ 7. items
def item_doc(code, name, group, uom, rate, *, stock=1, sell=None, wh_name="Raw Material Store"):
    doc = {"doctype": "Item", "item_code": code, "item_name": name[:140], "item_group": group,
           "stock_uom": uom, "is_stock_item": stock, "include_item_in_manufacturing": 1,
           "valuation_rate": rate or 0, "description": name,
           "item_defaults": [{"company": C.COMPANY, "default_warehouse": wh(wh_name)}]}
    if sell:
        doc["standard_rate"] = sell
    return doc


REORDER = {  # code: (reorder level, reorder qty) in Raw Material / Hardware store
    "PB-18-PRELAM": (60, 160), "PLY-18-COM": (25, 60), "BRD-UV-18": (15, 40), "PLY-06-BACK": (20, 50),
    "LAM-1MM-MARBLE": (15, 40), "LAM-1MM-MATTE": (15, 40), "LAM-08-LINER": (20, 50),
    "HW-HINGE-SC": (150, 400), "HW-HANDLE": (50, 150), "EDGE-PVC-2MM": (800, 2000),
}


def items():
    for code, name, group, uom, rate in C.RAW_MATERIALS:
        if not erp.exists("Item", code):
            store = "Hardware Store" if group == "Hardware" else "Raw Material Store"
            doc = item_doc(code, name, group, uom_for(code, group, uom), rate, wh_name=store)
            if code in REORDER:
                lvl, qty = REORDER[code]
                doc["reorder_levels"] = [{"warehouse": wh(store), "warehouse_reorder_level": lvl,
                                          "warehouse_reorder_qty": qty, "material_request_type": "Purchase"}]
            erp.insert(doc)
    for code, name, group, uom, rate in C.SCRAP_ITEMS:
        if not erp.exists("Item", code):
            erp.insert(item_doc(code, name, group, uom, rate, wh_name="Scrap Yard"))
    for code, name, group, uom in C.SUB_ASSEMBLIES:
        if not erp.exists("Item", code):
            erp.insert(item_doc(code, name, group, "Sheet", 0, wh_name="Raw Material Store"))
    for code, name, group, rate in C.FINISHED_GOODS + C.SCRAP_PRODUCTS:
        if not erp.exists("Item", code):
            erp.insert(item_doc(code, name, group, "Nos", 0, sell=rate, wh_name="Finished Goods Store"))
        if not erp.list("Item Price", filters={"item_code": code, "price_list": "Standard Selling"}, fields=["name"]):
            erp.insert({"doctype": "Item Price", "item_code": code, "price_list": "Standard Selling", "price_list_rate": rate, "valid_from": "2026-04-01"})
    for code, name, group, uom, rate in C.RAW_MATERIALS:
        if not erp.list("Item Price", filters={"item_code": code, "price_list": "Standard Buying"}, fields=["name"]):
            erp.insert({"doctype": "Item Price", "item_code": code, "price_list": "Standard Buying", "price_list_rate": rate, "valid_from": "2026-04-01"})
    log("items, reorder levels, selling and buying prices")


# ------------------------------------------------------------------ 8. BOMs
def boms():
    rates = {c: r for c, _, _, _, r in C.RAW_MATERIALS} | {c: r for c, _, _, _, r in C.SCRAP_ITEMS}
    order = ["SA-DOOR-PRESSED"] + [k for k in C.BOMS if k != "SA-DOOR-PRESSED"]
    for item in order:
        existing = erp.list("BOM", filters={"item": item, "docstatus": 1}, fields=["name"])
        for i, b in enumerate(C.BOMS[item]):
            if i < len(existing):
                continue
            doc = {"doctype": "BOM", "item": item, "quantity": 1, "company": C.COMPANY,
                   "is_active": 1, "is_default": 1 if i == 0 else 0, "with_operations": 1,
                   "rm_cost_as_per": "Valuation Rate",
                   "items": [{"item_code": c, "qty": q} for c, q in b["items"]],
                   "operations": [{"operation": op, "workstation": C.OPERATIONS[op], "time_in_mins": h * 60}
                                  for op, h in b["ops"]],
                   "secondary_items": [{"item_code": c, "secondary_item_type": "Scrap", "qty": q,
                                        "valuation_type": "Valuation Rate"} for c, q in b["scrap"]]}
            sub = erp.submit(doc)
            log(f"BOM {sub['name']} ({b['variant']}) total cost {sub.get('total_cost')}")


# ------------------------------------------------------------------ 9. quality inspection before dispatch
QI_PARAMS = ["Handle alignment", "Finish fully dry", "Edge banding intact", "No chipping or scratches",
             "Hardware complete", "Matches order specification"]


def quality():
    for p in QI_PARAMS:
        if not erp.exists("Quality Inspection Parameter", p):
            erp.insert({"doctype": "Quality Inspection Parameter", "parameter": p})
    tpl = "Pre-Dispatch Furniture Check"
    if not erp.exists("Quality Inspection Template", tpl):
        erp.insert({"doctype": "Quality Inspection Template", "quality_inspection_template_name": tpl,
                    "item_quality_inspection_parameter": [
                        {"specification": p, "numeric": 0, "value": "OK"} for p in QI_PARAMS]})
    for code, *_ in C.FINISHED_GOODS:
        erp.update("Item", code, {"quality_inspection_template": tpl, "inspection_required_before_delivery": 1})
    log("pre-dispatch quality inspection on all finished goods")


# ------------------------------------------------------------------ 10. customisations
SO_FIELDS = [
    ("custom_order_kind", "Order Kind", "Select", "Standard\nCustom", "customer"),
    ("custom_spec_section", "Custom Order Specification", "Section Break", None, "items"),
    ("custom_retailer_staff", "Retailer Sales Staff", "Data", None, "custom_spec_section"),
    ("custom_measured_by", "Measured By (factory)", "Data", None, "custom_retailer_staff"),
    ("custom_measured_on", "Measured On", "Date", None, "custom_measured_by"),
    ("custom_col1", None, "Column Break", None, "custom_measured_on"),
    ("custom_site_width_mm", "Site Width (mm)", "Int", None, "custom_col1"),
    ("custom_site_height_mm", "Site Height (mm)", "Int", None, "custom_site_width_mm"),
    ("custom_site_depth_mm", "Site Depth (mm)", "Int", None, "custom_site_height_mm"),
    ("custom_col2", None, "Column Break", None, "custom_site_depth_mm"),
    ("custom_finish", "Finish / Colour", "Data", None, "custom_col2"),
    ("custom_internal_layout", "Internal Layout (shelves, drawers, hanging)", "Small Text", None, "custom_finish"),
    ("custom_design_drawing", "Design Drawing", "Attach Image", None, "custom_internal_layout"),
    ("custom_spec_version", "Spec Version", "Int", None, "custom_design_drawing"),
    ("custom_customer_signature", "Customer Sign-off", "Signature", None, "custom_spec_version"),
    ("custom_signed_on", "Signed On", "Date", None, "custom_customer_signature"),
]

SIGNOFF_SCRIPT = '''
if doc.custom_order_kind == "Custom":
    required = {
        "custom_site_width_mm": "site width", "custom_site_height_mm": "site height",
        "custom_site_depth_mm": "site depth", "custom_finish": "finish",
        "custom_internal_layout": "internal layout", "custom_design_drawing": "design drawing",
        "custom_customer_signature": "customer sign-off",
    }
    missing = [label for field, label in required.items() if not doc.get(field)]
    if missing:
        frappe.throw("Custom order cannot be confirmed until the specification is complete and signed. Missing: " + ", ".join(missing))
'''


def custom_doctype(name, fields, autoname):
    if erp.exists("DocType", name):
        return
    erp.insert({"doctype": "DocType", "name": name, "module": "Stock", "custom": 1, "autoname": autoname,
                "naming_rule": "Expression", "track_changes": 1,
                "fields": fields,
                "permissions": [{"role": r, "read": 1, "write": 1, "create": 1, "delete": 1}
                                for r in ("System Manager", "Stock Manager")]})


def customisations():
    for fieldname, label, ftype, options, after in SO_FIELDS:
        if not erp.list("Custom Field", filters={"dt": "Sales Order", "fieldname": fieldname}, fields=["name"]):
            f = {"doctype": "Custom Field", "dt": "Sales Order", "fieldname": fieldname, "fieldtype": ftype,
                 "insert_after": after}
            if label:
                f["label"] = label
            if options:
                f["options"] = options
                f["default"] = "Standard"
            if fieldname not in ("custom_order_kind",):
                f["depends_on"] = "eval:doc.custom_order_kind=='Custom'"
            erp.insert(f)
    subprocess.run(["docker", "exec", "kfw-erp-backend-1", "bench", "set-config", "-g", "server_script_enabled", "1"],
                   check=True, capture_output=True)
    if not erp.exists("Server Script", "Custom Order Sign-off Gate"):
        erp.insert({"doctype": "Server Script", "name": "Custom Order Sign-off Gate", "script_type": "DocType Event",
                    "reference_doctype": "Sales Order", "doctype_event": "Before Submit", "script": SIGNOFF_SCRIPT})
    custom_doctype("Transit Damage Log", [
        {"fieldname": "posting_date", "label": "Date", "fieldtype": "Date", "reqd": 1, "in_list_view": 1},
        {"fieldname": "customer", "label": "Retailer", "fieldtype": "Link", "options": "Customer", "in_list_view": 1},
        {"fieldname": "item_code", "label": "Item", "fieldtype": "Link", "options": "Item", "reqd": 1, "in_list_view": 1},
        {"fieldname": "qty", "label": "Qty", "fieldtype": "Float", "default": "1"},
        {"fieldname": "cause", "label": "Cause", "fieldtype": "Select", "in_list_view": 1,
         "options": "Transit chipping\nWet finish smudged\nCrooked handle drilling\nEdge banding peeled\nWrong specification"},
        {"fieldname": "action", "label": "Action", "fieldtype": "Select", "options": "Full replacement\nOn-site repair"},
        {"fieldname": "replacement_cost", "label": "Replacement Cost", "fieldtype": "Currency", "in_list_view": 1},
        {"fieldname": "notes", "label": "Notes", "fieldtype": "Small Text"},
    ], "format:DMG-{#####}")
    custom_doctype("Production Downtime", [
        {"fieldname": "posting_date", "label": "Date", "fieldtype": "Date", "reqd": 1, "in_list_view": 1},
        {"fieldname": "reason", "label": "Reason", "fieldtype": "Select", "in_list_view": 1,
         "options": "No sheets in stock\nMachine breakdown\nPower cut"},
        {"fieldname": "hours_lost", "label": "Hours Lost", "fieldtype": "Float"},
        {"fieldname": "workers_idle", "label": "Workers Idle", "fieldtype": "Int"},
        {"fieldname": "idle_cost", "label": "Idle Cost (salary + fixed)", "fieldtype": "Currency", "in_list_view": 1},
        {"fieldname": "repair_cost", "label": "Repair Cost", "fieldtype": "Currency"},
        {"fieldname": "notes", "label": "Notes", "fieldtype": "Small Text"},
    ], "format:DT-{#####}")
    log("custom-order specification fields + sign-off gate, Transit Damage Log, Production Downtime")


def main():
    global erp
    erp = ERP()
    print("Configuring ERPNext for", C.COMPANY)
    for step in (setup_wizard, accounts_and_tax, basics, warehouses, parties, production_setup,
                 items, boms, quality, customisations, roles):
        print(step.__name__)
        try:
            step()
        except ERPError as e:
            print(f"  ! {step.__name__} failed: {e}")
            raise



# ------------------------------------------------------------------ 11. roles (who sees what)
ROLE_PROFILES = {
    "Factory Owner": ["Accounts Manager", "Sales Manager", "Purchase Manager", "Stock Manager",
                      "Manufacturing Manager", "Quality Manager", "Report Manager"],
    "Factory Accountant": ["Accounts User", "Sales User", "Purchase User", "Stock User"],
    "Store and Floor": ["Stock User", "Manufacturing User", "Quality Manager"],
    "Retail Floor Sales": ["Sales User"],
}
DEMO_USERS = [  # local demo accounts, no passwords, no e-mail sent
    ("owner@kfw.test", "Factory", "Owner", "Factory Owner"),
    ("accounts@kfw.test", "Factory", "Accountant", "Factory Accountant"),
    ("stores@kfw.test", "Store", "Supervisor", "Store and Floor"),
    ("padi.sales@kfw.test", "Padi", "Sales Staff", "Retail Floor Sales"),
]


def roles():
    for profile, rlist in ROLE_PROFILES.items():
        if not erp.exists("Role Profile", profile):
            erp.insert({"doctype": "Role Profile", "role_profile": profile,
                        "roles": [{"role": r} for r in rlist]})
    for email, first, last, profile in DEMO_USERS:
        if not erp.exists("User", email):
            erp.insert({"doctype": "User", "email": email, "first_name": first, "last_name": last,
                        "send_welcome_email": 0, "user_type": "System User",
                        "role_profile_name": profile, "roles": [{"role": r} for r in ROLE_PROFILES[profile]]})
    log("role profiles and demo users")


if __name__ == "__main__":
    main()
