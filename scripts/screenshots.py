"""Capture screenshots of the configured ERPNext screens into docs/img (local site, headless Chrome)."""
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from erp import BASE, ERP

IMG = Path(__file__).resolve().parent.parent / "docs" / "img"
IMG.mkdir(parents=True, exist_ok=True)
F = "from_date=2026-10-01&to_date=2026-10-31"
erp = ERP()

custom_signed = erp.list("Sales Order", filters={"custom_order_kind": "Custom", "docstatus": 1}, fields=["name"],
                         order_by="creation asc")[0]["name"]
# a fresh unsigned custom order, used only to show the sign-off gate in the UI, deleted afterwards
draft = erp.insert({"doctype": "Sales Order", "customer": "Kovai Home Centre", "company": "Kaveri Furniture Works",
                    "transaction_date": "2026-10-28", "delivery_date": "2026-11-18", "custom_order_kind": "Custom",
                    "custom_retailer_staff": "Kovai floor staff", "custom_site_width_mm": 2440,
                    "custom_site_height_mm": 2400, "custom_finish": "Teal gloss", "ignore_pricing_rule": 1,
                    "set_warehouse": "Finished Goods Store - KFW",
                    "items": [{"item_code": "WIW-CUSTOM", "qty": 1, "rate": 109300, "delivery_date": "2026-11-18"}]})

pending_po = erp.list("Purchase Order", filters={"workflow_state": "Pending Approval"}, fields=["name"])
if not pending_po:
    tx = erp.call("erpnext.controllers.accounts_controller.get_taxes_and_charges",
                  master_doctype="Purchase Taxes and Charges Template", master_name="IGST 18% Inter-State - KFW")
    po = erp.insert({"doctype": "Purchase Order", "supplier": "Sabarmati Laminates Wholesale", "company": "Kaveri Furniture Works",
                     "transaction_date": "2026-11-04", "schedule_date": "2026-11-16",
                     "taxes_and_charges": "IGST 18% Inter-State - KFW", "taxes": tx,
                     "items": [{"item_code": "PB-18-PRELAM", "qty": 170, "rate": 1450, "schedule_date": "2026-11-16",
                                "warehouse": "Raw Material Store - KFW"},
                               {"item_code": "BRD-UV-18", "qty": 20, "rate": 4300, "schedule_date": "2026-11-16",
                                "warehouse": "Raw Material Store - KFW"}]})
    import json as _json
    erp.call("frappe.model.workflow.apply_workflow", doc=_json.dumps(po), action="Submit for Approval")
    pending_po = [{"name": po["name"]}]
plan_name = erp.list("Production Plan", filters={"docstatus": 1}, fields=["name"], limit=1)[0]["name"]

SHOTS = [
    ("14-po-pending-owner-approval", f"/desk/purchase-order/{pending_po[0]['name']}", None),
    ("15-production-plan", f"/desk/production-plan/{plan_name}", "Material Request Planning"),
    ("16-material-requests", "/desk/material-request", None),
    ("01-owner-dashboard", "/desk/owner-dashboard", None),
    ("02-daily-sales-and-profit", f"/desk/query-report/{quote('Daily Sales and Profit')}?{F}", None),
    ("03-margin-leaks", f"/desk/query-report/{quote('Margin Leaks')}?{F}", None),
    ("04-product-profitability", f"/desk/query-report/{quote('Product Profitability')}?{F}", None),
    ("05-make-or-buy", f"/desk/query-report/{quote('Make or Buy - Door Boards')}?{F}", None),
    ("06-custom-order-signed-spec", f"/desk/sales-order/{custom_signed}", "Design Drawing"),
    ("08-bom-wardrobe", "/desk/bom/BOM-WRD-3D-001", "Operations"),
    ("09-production-downtime", "/desk/production-downtime", None),
    ("10-transit-damage-log", "/desk/transit-damage-log", None),
    ("11-quality-inspections", "/desk/quality-inspection?status=Rejected", None),
    ("12-accounts-receivable", "/desk/query-report/Accounts%20Receivable", None),
]

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    # reuse the API session (same login) instead of driving the login form
    ctx.add_cookies([{"name": c.name, "value": c.value, "url": BASE} for c in erp.s.cookies])
    page = ctx.new_page()
    for name, path, scroll_to in SHOTS:
        page.goto(f"{BASE}{path}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500)
        if scroll_to:
            loc = page.get_by_text(scroll_to, exact=True).first
            if loc.count():
                loc.scroll_into_view_if_needed()
                page.mouse.wheel(0, -120)
                page.wait_for_timeout(800)
        page.screenshot(path=str(IMG / f"{name}.png"))
        print("saved", name)

    # sign-off gate as the user sees it
    page.goto(f"{BASE}/desk/sales-order/{draft['name']}")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    page.get_by_role("button", name="Submit").first.click()
    page.wait_for_timeout(1200)
    yes = page.get_by_role("button", name="Yes")
    if yes.count():
        yes.first.click()
    page.wait_for_timeout(2500)
    page.screenshot(path=str(IMG / "07-custom-order-gate-blocks-unsigned.png"))
    print("saved 07-custom-order-gate-blocks-unsigned")
    browser.close()

erp.s.delete(f"{BASE}/api/resource/Sales Order/{draft['name']}")
