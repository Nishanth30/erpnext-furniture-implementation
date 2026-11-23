"""Migrate the accountant's legacy Excel files into ERPNext (go-live: 30 Sep 2026).

Pipeline per file:  read -> normalise -> validate -> (load | write to exceptions)
Nothing invalid is loaded silently: every rejected or corrected row lands in
out/migration_exceptions.csv with a reason, for the accountant to resolve.

Idempotent: opening documents carry the legacy reference and are skipped if already loaded.
"""
import csv
import re
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

import catalog as C
from erp import ERP

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "data" / "legacy"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
A = C.ABBR
GO_LIVE = "2026-09-30"
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

exceptions = []
summary = {}


def exc(source, row, field, value, reason, action):
    exceptions.append({"file": source, "row": row, "field": field, "value": value, "reason": reason, "action": action})


def read(name):
    ws = load_workbook(LEGACY / name, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    return [dict(zip(header, r)) | {"_row": i + 2} for i, r in enumerate(rows[1:])]


def norm_name(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def norm_key(s):
    return re.sub(r"[^a-z0-9]", "", norm_name(s).lower())


def to_number(v):
    """'Rs.1,450' / '₹ 1450' / 1450 -> 1450.0"""
    if isinstance(v, (int, float)):
        return float(v)
    return float(re.sub(r"(?i)^\s*(rs\.?|inr|₹)\s*|,", "", str(v)).strip())


def to_date(v):
    return v.date().isoformat() if isinstance(v, datetime) else datetime.strptime(str(v), "%d-%m-%Y").date().isoformat()


def alias_map():
    with open(ROOT / "data" / "item_alias_map.csv") as f:
        return {norm_key(r["legacy_name"]): r["item_code"] for r in csv.DictReader(f)}


# ------------------------------------------------------------------ items
def check_items(erp, aliases):
    rows = read("Item_List.xlsx")
    mapped = unmapped = rate_fixed = 0
    for r in rows:
        code = aliases.get(norm_key(r["Item"]))
        if not code:
            unmapped += 1
            exc("Item_List.xlsx", r["_row"], "Item", r["Item"], "no matching item in new item master", "map or create item")
            continue
        mapped += 1
        if isinstance(r["Last Rate"], str):
            rate_fixed += 1
            exc("Item_List.xlsx", r["_row"], "Last Rate", r["Last Rate"], "text in number cell ('Rs.')",
                f"cleaned to {to_number(r['Last Rate'])}")
    summary["items"] = {"legacy rows": len(rows), "mapped to new item codes": mapped, "unmapped": unmapped,
                        "rates cleaned": rate_fixed, "new item master size": len(erp.list("Item", fields=["name"]))}


# ------------------------------------------------------------------ parties
def migrate_parties(erp):
    rows = read("Party_List.xlsx")
    erp_customers = {norm_key(c["name"]): c["name"] for c in erp.list("Customer", fields=["name"])}
    erp_suppliers = {norm_key(s["name"]): s["name"] for s in erp.list("Supplier", fields=["name"])}
    seen, updated, dup, bad_gst = set(), 0, 0, 0
    for r in rows:
        key = norm_key(r["Name"])
        table, lookup = ("Customer", erp_customers) if r["Type"] == "Retailer" else ("Supplier", erp_suppliers)
        if key in seen:
            dup += 1
            exc("Party_List.xlsx", r["_row"], "Name", r["Name"], "duplicate party (case/spacing variant)", "merged into first record")
            continue
        seen.add(key)
        name = lookup.get(key)
        if not name:
            exc("Party_List.xlsx", r["_row"], "Name", r["Name"], "party not in approved master", "review")
            continue
        gst = (r["GSTIN"] or "").strip().upper()
        values = {}
        if not gst:
            bad_gst += 1
            exc("Party_List.xlsx", r["_row"], "GSTIN", "", "missing GSTIN (blocks input-tax credit / e-invoice)", "collect from party")
        elif not GSTIN_RE.match(gst):
            bad_gst += 1
            exc("Party_List.xlsx", r["_row"], "GSTIN", gst, "malformed GSTIN (fails 15-char pattern)", "not loaded; confirm with party")
        else:
            values["tax_id"] = gst
        if r["Phone"]:
            values["mobile_no"] = str(r["Phone"])
        if values:
            erp.update(table, name, values)
            updated += 1
    summary["parties"] = {"legacy rows": len(rows), "updated in ERP": updated, "duplicates merged": dup,
                          "GSTIN problems": bad_gst}


# ------------------------------------------------------------------ opening stock
def migrate_stock(erp, aliases):
    existing = erp.list("Stock Reconciliation", filters={"purpose": "Opening Stock", "docstatus": 1}, fields=["name"])
    rows = read("Stock_Count_Sep30.xlsx")
    rates = {c: r for c, _, _, _, r in C.RAW_MATERIALS}
    groups = {c: g for c, _, g, _, _ in C.RAW_MATERIALS}
    lines, rejected = [], 0
    for r in rows:
        code = aliases.get(norm_key(r["Item"]))
        qty = to_number(r["Qty"])
        if not code:
            rejected += 1
            exc("Stock_Count_Sep30.xlsx", r["_row"], "Item", r["Item"], "unknown item", "not loaded; identify item")
            continue
        if qty < 0:
            rejected += 1
            exc("Stock_Count_Sep30.xlsx", r["_row"], "Qty", qty, "negative stock count", "not loaded; recount")
            continue
        store = "Hardware Store" if groups[code] == "Hardware" else "Raw Material Store"
        lines.append({"item_code": code, "warehouse": f"{store} - {A}", "qty": qty, "valuation_rate": rates[code]})
    doc = {"doctype": "Stock Reconciliation", "company": C.COMPANY, "purpose": "Opening Stock",
           "posting_date": GO_LIVE, "posting_time": "23:00:00", "set_posting_time": 1,
           "expense_account": f"Temporary Opening - {A}", "items": lines}
    name = existing[0]["name"] if existing else erp.submit(doc)["name"]   # idempotent re-run
    value = sum(l["qty"] * l["valuation_rate"] for l in lines)
    summary["stock"] = {"legacy rows": len(rows), "loaded lines": len(lines), "rejected": rejected,
                        "opening stock value (₹)": round(value), "document": name}


# ------------------------------------------------------------------ opening receivables / payables
def migrate_outstanding(erp):
    rows = read("Outstanding_Sep30.xlsx")
    customers = {norm_key(c["name"]): c["name"] for c in erp.list("Customer", fields=["name"])}
    suppliers = {norm_key(s["name"]): s["name"] for s in erp.list("Supplier", fields=["name"])}
    loaded = {"Receivable": [0, 0.0], "Payable": [0, 0.0]}
    rejected = 0
    for r in rows:
        amount = to_number(r["Amount (incl GST)"])
        bill_date = to_date(r["Bill Date"])
        ref = str(r["Bill No"]).strip()
        if r["Type"] == "Receivable":
            party = customers.get(norm_key(r["Party"]))
            if not party:
                rejected += 1
                exc("Outstanding_Sep30.xlsx", r["_row"], "Party", r["Party"], "receivable for unknown retailer",
                    "not loaded; confirm party or write off")
                continue
            if not erp.list("Sales Invoice", filters={"remarks": f"Opening: {ref}", "docstatus": 1}, fields=["name"]):
              erp.submit({"doctype": "Sales Invoice", "customer": party, "company": C.COMPANY, "is_opening": "Yes",
                        "posting_date": GO_LIVE, "set_posting_time": 1, "due_date": GO_LIVE,
                        "remarks": f"Opening: {ref}", "taxes_and_charges": "",
                        "items": [{"item_name": f"Opening balance {ref} ({bill_date})", "description": f"Legacy bill {ref}",
                                   "qty": 1, "rate": amount, "uom": "Nos",
                                   "income_account": f"Temporary Opening - {A}"}]})
        else:
            party = suppliers.get(norm_key(r["Party"]))
            if not party:
                rejected += 1
                exc("Outstanding_Sep30.xlsx", r["_row"], "Party", r["Party"], "payable for unknown supplier", "review")
                continue
            if not erp.list("Purchase Invoice", filters={"bill_no": ref, "supplier": party, "docstatus": 1}, fields=["name"]):
              erp.submit({"doctype": "Purchase Invoice", "supplier": party, "company": C.COMPANY, "is_opening": "Yes",
                        "posting_date": GO_LIVE, "set_posting_time": 1, "due_date": GO_LIVE,
                        "bill_no": ref, "bill_date": bill_date, "taxes_and_charges": "",
                        "items": [{"item_name": f"Opening balance {ref}", "description": f"Legacy bill {ref}",
                                   "qty": 1, "rate": amount, "uom": "Nos",
                                   "expense_account": f"Temporary Opening - {A}"}]})
        loaded[r["Type"]][0] += 1
        loaded[r["Type"]][1] += amount
    summary["outstanding"] = {"legacy rows": len(rows), "receivables loaded": loaded["Receivable"][0],
                              "receivable value (₹)": round(loaded["Receivable"][1]),
                              "payables loaded": loaded["Payable"][0],
                              "payable value (₹)": round(loaded["Payable"][1]), "rejected": rejected}


def main():
    erp = ERP()
    aliases = alias_map()
    for step, fn in [("items", lambda: check_items(erp, aliases)), ("parties", lambda: migrate_parties(erp)),
                     ("stock", lambda: migrate_stock(erp, aliases)), ("outstanding", lambda: migrate_outstanding(erp))]:
        print("migrating", step)
        fn()
    with open(OUT / "migration_exceptions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "row", "field", "value", "reason", "action"])
        w.writeheader()
        w.writerows(exceptions)
    with open(OUT / "migration_summary.md", "w") as f:
        f.write("# Migration summary (go-live 30 Sep 2026)\n\n")
        for section, vals in summary.items():
            f.write(f"## {section}\n\n| Measure | Value |\n|---|---|\n")
            for k, v in vals.items():
                f.write(f"| {k} | {v} |\n")
            f.write("\n")
        f.write(f"Exceptions logged: {len(exceptions)} (see migration_exceptions.csv)\n")
    print(open(OUT / "migration_summary.md").read())


if __name__ == "__main__":
    main()
