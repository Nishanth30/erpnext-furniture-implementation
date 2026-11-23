"""Owner-facing reports and dashboard inside ERPNext (idempotent).

Query Reports (SQL, run inside ERPNext, visible to the Factory Owner role):
  1. Daily Sales and Profit        - the owner's ask: what sold and what it earned, per day
  2. Product Profitability         - real margin per product after making cost
  3. Margin Leaks                  - where money disappears between the price and the bank
  4. Make or Buy - Door Boards     - in-house cold press vs bought UV board, from actual production
Plus number cards, charts and an 'Owner Dashboard' workspace.
"""
import json

import catalog as C
from erp import ERP

A = C.ABBR
erp = ERP()
DATE_FILTERS = [
    {"fieldname": "from_date", "label": "From Date", "fieldtype": "Date", "mandatory": 1, "default": "2026-10-01"},
    {"fieldname": "to_date", "label": "To Date", "fieldtype": "Date", "mandatory": 1, "default": "2026-10-31"},
]

REPORTS = {
    "Daily Sales and Profit": ("Sales Invoice", DATE_FILTERS, f"""
SELECT si.posting_date AS `Date:Date:110`,
       COUNT(DISTINCT si.name) AS `Invoices:Int:80`,
       SUM(sii.stock_qty) AS `Units:Float:70`,
       SUM(sii.base_net_amount) AS `Net Sales ex-GST:Currency:140`,
       SUM(sii.stock_qty * IFNULL(dni.incoming_rate, 0)) AS `Cost of Goods:Currency:140`,
       SUM(sii.base_net_amount) - SUM(sii.stock_qty * IFNULL(dni.incoming_rate, 0)) AS `Gross Profit:Currency:140`,
       ROUND(100 * (SUM(sii.base_net_amount) - SUM(sii.stock_qty * IFNULL(dni.incoming_rate, 0)))
             / NULLIF(SUM(sii.base_net_amount), 0), 1) AS `GP %%:Percent:80`,
       SUM(sii.base_net_amount) * 1.18 AS `Turnover incl GST:Currency:140`
FROM `tabSales Invoice` si
JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
LEFT JOIN `tabDelivery Note Item` dni ON dni.name = sii.dn_detail
WHERE si.docstatus = 1 AND si.is_opening = 'No'
  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
GROUP BY si.posting_date
ORDER BY si.posting_date
"""),
    "Product Profitability": ("Sales Invoice", DATE_FILTERS, f"""
SELECT sii.item_code AS `Item:Link/Item:130`,
       sii.item_name AS `Item Name::220`,
       SUM(sii.stock_qty) AS `Units:Float:70`,
       ROUND(SUM(sii.base_net_amount) / NULLIF(SUM(sii.stock_qty), 0)) AS `Avg Price ex-GST:Currency:130`,
       ROUND(SUM(sii.stock_qty * IFNULL(dni.incoming_rate, 0)) / NULLIF(SUM(sii.stock_qty), 0)) AS `Avg Full Cost:Currency:120`,
       SUM(sii.base_net_amount) - SUM(sii.stock_qty * IFNULL(dni.incoming_rate, 0)) AS `Gross Profit:Currency:130`,
       ROUND(100 * (SUM(sii.base_net_amount) / NULLIF(SUM(sii.stock_qty * IFNULL(dni.incoming_rate, 0)), 0) - 1), 1)
           AS `Markup on Cost %%:Percent:120`
FROM `tabSales Invoice` si
JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
LEFT JOIN `tabDelivery Note Item` dni ON dni.name = sii.dn_detail
WHERE si.docstatus = 1 AND si.is_opening = 'No'
  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
GROUP BY sii.item_code, sii.item_name
ORDER BY `Gross Profit:Currency:130` DESC
"""),
    "Margin Leaks": ("GL Entry", DATE_FILTERS, f"""
SELECT * FROM (
  SELECT 'A. Hidden fixed cost' AS `Type::150`,
         'Fixed costs not recovered in product cost' AS `Leak::330`,
         (SELECT IFNULL(SUM(debit - credit), 0) FROM `tabGL Entry` WHERE is_cancelled = 0
            AND account IN ('Salary - {A}', 'Office Rent - {A}', 'Utility Expenses - {A}', 'Miscellaneous Expenses - {A}')
            AND posting_date BETWEEN %(from_date)s AND %(to_date)s)
       - (SELECT IFNULL(SUM(credit - debit), 0) FROM `tabGL Entry` WHERE is_cancelled = 0
            AND account = 'Expenses Included In Valuation - {A}' AND voucher_type = 'Stock Entry'
            AND posting_date BETWEEN %(from_date)s AND %(to_date)s) AS `Amount:Currency:150`,
         'Salaries, rent, power, other paid minus what production absorbed at machine-hour rates' AS `How it is measured::430`
  UNION ALL
  SELECT 'A. of which', '... idle days: no sheets in stock', IFNULL(SUM(idle_cost), 0), 'Production Downtime log (subset of A)'
    FROM `tabProduction Downtime` WHERE reason = 'No sheets in stock' AND posting_date BETWEEN %(from_date)s AND %(to_date)s
  UNION ALL
  SELECT 'A. of which', '... idle time: machine breakdown', IFNULL(SUM(idle_cost), 0), 'Production Downtime log (subset of A)'
    FROM `tabProduction Downtime` WHERE reason = 'Machine breakdown' AND posting_date BETWEEN %(from_date)s AND %(to_date)s
  UNION ALL
  SELECT 'B. Direct loss', 'Machine repair visits', IFNULL(SUM(repair_cost), 0), 'Production Downtime log'
    FROM `tabProduction Downtime` WHERE posting_date BETWEEN %(from_date)s AND %(to_date)s
  UNION ALL
  SELECT 'B. Direct loss', 'Transit damage: free replacements', IFNULL(SUM(replacement_cost), 0), 'Transit Damage Log'
    FROM `tabTransit Damage Log` WHERE posting_date BETWEEN %(from_date)s AND %(to_date)s
  UNION ALL
  SELECT 'B. Direct loss', 'Early-payment discounts given', IFNULL(SUM(debit - credit), 0), 'GL: Early Payment Discount Allowed'
    FROM `tabGL Entry` WHERE is_cancelled = 0 AND account = 'Early Payment Discount Allowed - {A}'
     AND posting_date BETWEEN %(from_date)s AND %(to_date)s
  UNION ALL
  SELECT 'B. Direct loss', 'Hardware missing at month-end count', IFNULL(SUM(debit - credit), 0), 'GL: Stock Adjustment (physical count)'
    FROM `tabGL Entry` WHERE is_cancelled = 0 AND account = 'Stock Adjustment - {A}' AND voucher_type = 'Stock Reconciliation'
     AND posting_date BETWEEN %(from_date)s AND %(to_date)s
  UNION ALL
  SELECT 'C. Inside product cost', 'Local emergency sheet buys (premium over bulk rate)',
         IFNULL(SUM(pii.base_net_amount - pii.stock_qty * ip.price_list_rate), 0), 'Local supplier invoices vs bulk price list'
    FROM `tabPurchase Invoice` pi JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
    JOIN `tabItem Price` ip ON ip.item_code = pii.item_code AND ip.price_list = 'Standard Buying'
   WHERE pi.docstatus = 1 AND pi.supplier = 'Madras Ply and Lam Traders' AND pi.is_opening = 'No'
     AND pi.posting_date BETWEEN %(from_date)s AND %(to_date)s
) leaks
"""),
    "Make or Buy - Door Boards": ("Stock Entry", DATE_FILTERS, f"""
SELECT se.bom_no AS `BOM:Link/BOM:170`,
       CASE se.bom_no WHEN 'BOM-WRD-3D-001' THEN 'Buy UV door board' ELSE 'Press door board in-house' END AS `Route::200`,
       SUM(sed.qty) AS `Wardrobes Made:Float:120`,
       ROUND(SUM(sed.amount) / SUM(sed.qty)) AS `Actual Cost per Wardrobe:Currency:170`
FROM `tabStock Entry` se
JOIN `tabStock Entry Detail` sed ON sed.parent = se.name AND sed.is_finished_item = 1
WHERE se.docstatus = 1 AND se.purpose = 'Manufacture' AND sed.item_code = 'WRD-3D'
  AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s
GROUP BY se.bom_no
"""),
}


def upsert_report(name, ref, filters, query):
    doc = {"report_type": "Query Report", "ref_doctype": ref, "is_standard": "No", "module": "Stock",
           "query": query.strip(), "filters": filters,
           "roles": [{"role": r} for r in ("System Manager", "Accounts Manager", "Sales Manager")]}
    if erp.exists("Report", name):
        erp.update("Report", name, doc)
    else:
        erp.insert(dict(doc, doctype="Report", report_name=name))


def upsert(doctype, name, doc):
    if erp.exists(doctype, name):
        erp.update(doctype, name, doc)
    else:
        erp.insert(dict(doc, doctype=doctype, name=name))


LAST_MONTH = ["Timespan", "last month"]
CARDS = [
    ("Sales Last Month ex-GST", {"document_type": "Sales Invoice", "function": "Sum",
                                 "aggregate_function_based_on": "base_net_total",
                                 "filters_json": json.dumps([["Sales Invoice", "docstatus", "=", 1],
                                                             ["Sales Invoice", "is_opening", "=", "No"],
                                                             ["Sales Invoice", "posting_date"] + LAST_MONTH])}),
    ("Retailers Owe Us", {"document_type": "Sales Invoice", "function": "Sum",
                          "aggregate_function_based_on": "outstanding_amount",
                          "filters_json": json.dumps([["Sales Invoice", "docstatus", "=", 1]])}),
    ("We Owe Suppliers", {"document_type": "Purchase Invoice", "function": "Sum",
                          "aggregate_function_based_on": "outstanding_amount",
                          "filters_json": json.dumps([["Purchase Invoice", "docstatus", "=", 1]])}),
    ("Transit Damage Cost", {"document_type": "Transit Damage Log", "function": "Sum",
                             "aggregate_function_based_on": "replacement_cost", "filters_json": "[]"}),
    ("Idle and Breakdown Cost", {"document_type": "Production Downtime", "function": "Sum",
                                 "aggregate_function_based_on": "idle_cost", "filters_json": "[]"}),
]
CHARTS = [
    ("Daily Sales ex-GST", {"chart_type": "Sum", "document_type": "Sales Invoice", "based_on": "posting_date",
                            "value_based_on": "base_net_total", "timeseries": 1, "time_interval": "Daily",
                            "timespan": "Select Date Range", "from_date": "2026-10-01", "to_date": "2026-10-31",
                            "type": "Bar",
                            "filters_json": json.dumps([["Sales Invoice", "docstatus", "=", 1],
                                                        ["Sales Invoice", "is_opening", "=", "No"]])}),
    ("Daily Gross Profit", {"chart_type": "Report", "report_name": "Daily Sales and Profit", "type": "Line",
                            "x_field": "date", "use_report_chart": 0,
                            "y_axis": [{"y_field": "gross_profit", "color": "#2490EF"}],
                            "filters_json": json.dumps({"from_date": "2026-10-01", "to_date": "2026-10-31"})}),
]


def dashboard():
    for name, doc in CARDS:
        upsert("Number Card", name, dict(doc, label=name, type="Document Type", is_public=1, show_percentage_stats=0))
    for name, doc in CHARTS:
        upsert("Dashboard Chart", name, dict(doc, chart_name=name, is_public=1))
    blocks = [{"id": "h1", "type": "header", "data": {"text": "<span class=\"h4\"><b>Owner Dashboard</b></span>", "col": 12}}]
    blocks += [{"id": f"c{i}", "type": "number_card", "data": {"number_card_name": n, "col": 4}} for i, (n, _) in enumerate(CARDS)]
    blocks += [{"id": f"g{i}", "type": "chart", "data": {"chart_name": n, "col": 12}} for i, (n, _) in enumerate(CHARTS)]
    blocks += [{"id": "h2", "type": "header", "data": {"text": "<span class=\"h4\"><b>Reports</b></span>", "col": 12}}]
    blocks += [{"id": f"s{i}", "type": "shortcut", "data": {"shortcut_name": n, "col": 3}} for i, n in enumerate(REPORTS)]
    ws = {"label": "Owner Dashboard", "title": "Owner Dashboard", "public": 1, "module": "Accounts", "is_hidden": 0, "icon": "chart",
          "content": json.dumps(blocks),
          "number_cards": [{"number_card_name": n, "label": n} for n, _ in CARDS],
          "charts": [{"chart_name": n, "label": n} for n, _ in CHARTS],
          "shortcuts": [{"type": "Report", "link_to": n, "label": n, "report_ref_doctype": REPORTS[n][0],
                         "is_query_report": 1} for n in REPORTS]}
    upsert("Workspace", "Owner Dashboard", ws)


def main():
    for name, (ref, filters, query) in REPORTS.items():
        upsert_report(name, ref, filters, query)
        res = erp.call("frappe.desk.query_report.run", report_name=name,
                       filters=json.dumps({"from_date": "2026-10-01", "to_date": "2026-10-31"}))
        print(f"report '{name}': {len(res.get('result', []))} rows")
    dashboard()
    print("owner dashboard workspace ready")


if __name__ == "__main__":
    main()
