"""Post-migration reconciliation: legacy files vs what ERPNext actually booked (SQL on the site DB).

Checks
 1. Opening stock qty and value per item  (cleaned count sheet  vs  Stock Ledger)
 2. Receivables per retailer              (cleaned dues sheet   vs  GL on Debtors)
 3. Payables per supplier                 (cleaned dues sheet   vs  GL on Creditors)
 4. Temporary Opening account balances to  stock + receivables - payables
Writes out/reconciliation_report.md
"""
import re
from collections import defaultdict
from pathlib import Path

import catalog as C
from erp import sql
from migrate import alias_map, norm_key, read, to_number

A = C.ABBR
OUT = Path(__file__).resolve().parent.parent / "out"
TOL = 0.5


def source_stock():
    aliases, rates = alias_map(), {c: r for c, _, _, _, r in C.RAW_MATERIALS}
    qty = {}
    for r in read("Stock_Count_Sep30.xlsx"):
        code = aliases.get(norm_key(r["Item"]))
        q = to_number(r["Qty"])
        if code and q >= 0:
            qty[code] = qty.get(code, 0) + q
    return {c: (q, q * rates[c]) for c, q in qty.items()}


def source_dues(kind):
    names = {norm_key(n): n for n, *_ in (C.CUSTOMERS if kind == "Receivable" else C.SUPPLIERS)}
    out = defaultdict(float)
    for r in read("Outstanding_Sep30.xlsx"):
        if r["Type"] == kind and norm_key(r["Party"]) in names:
            out[names[norm_key(r["Party"])]] += to_number(r["Amount (incl GST)"])
    return out


def main():
    lines, all_ok = ["# Migration reconciliation (go-live 30 Sep 2026)\n"], True

    def section(title, rows, header):
        nonlocal all_ok
        bad = [r for r in rows if r[-1] != "OK"]
        all_ok &= not bad
        lines.append(f"## {title}: {'PASS' if not bad else 'FAIL'} ({len(rows) - len(bad)}/{len(rows)} match)\n")
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        lines.extend("| " + " | ".join(str(x) for x in r) + " |" for r in rows)
        lines.append("")

    # 1. stock
    erp_stock = {r["item_code"]: (float(r["qty"]), float(r["value"])) for r in sql(
        "SELECT item_code, SUM(qty_after_transaction) qty, SUM(stock_value_difference) value FROM `tabStock Ledger Entry` "
        "WHERE voucher_type='Stock Reconciliation' AND is_cancelled=0 AND voucher_no IN "
        "(SELECT name FROM `tabStock Reconciliation` WHERE purpose='Opening Stock' AND docstatus=1) GROUP BY item_code")}  # opening reco rows carry qty in qty_after_transaction
    rows = []
    for code, (q, v) in sorted(source_stock().items()):
        eq, ev = erp_stock.get(code, (0, 0))
        ok = abs(eq - q) < TOL and abs(ev - v) < TOL
        rows.append((code, q, eq, round(v), round(ev), "OK" if ok else "MISMATCH"))
    section("Opening stock", rows, ["Item", "Source qty", "ERP qty", "Source value", "ERP value", "Status"])

    # 2/3. receivables and payables from the general ledger
    stock_total = sum(v for _, _, _, _, v, _ in rows)
    totals = {}
    for kind, account, sign in [("Receivable", f"Debtors - {A}", 1), ("Payable", f"Creditors - {A}", -1)]:
        gl = {r["party"]: sign * float(r["bal"]) for r in sql(
            f"SELECT party, SUM(debit - credit) bal FROM `tabGL Entry` WHERE account='{account}' "
            f"AND is_cancelled=0 AND posting_date <= '2026-09-30' GROUP BY party")}
        rows2 = []
        for party, amt in sorted(source_dues(kind).items()):
            e = gl.get(party, 0)
            rows2.append((party, round(amt), round(e), "OK" if abs(e - amt) < TOL else "MISMATCH"))
        totals[kind] = sum(r[2] for r in rows2)
        section(f"Opening {kind.lower()}s (GL: {account})", rows2, ["Party", "Source ₹", "ERP GL ₹", "Status"])

    # 4. temporary opening must net to zero against the three balances
    temp = sql(f"SELECT SUM(credit - debit) bal FROM `tabGL Entry` WHERE account='Temporary Opening - {A}' "
               f"AND is_cancelled=0 AND posting_date <= '2026-09-30'")
    temp_bal = float(temp[0]["bal"] or 0)
    expected = stock_total + totals["Receivable"] - totals["Payable"]
    ok = abs(temp_bal - expected) < 1
    all_ok &= ok
    lines.append(f"## Temporary Opening balance: {'PASS' if ok else 'FAIL'}\n")
    lines.append(f"Credit balance ₹{temp_bal:,.0f} = stock ₹{stock_total:,.0f} + receivables "
                 f"₹{totals['Receivable']:,.0f} − payables ₹{totals['Payable']:,.0f} (expected ₹{expected:,.0f}).\n")
    lines.insert(1, f"**Overall: {'PASS' if all_ok else 'FAIL'}**\n")
    (OUT / "reconciliation_report.md").write_text("\n".join(lines))
    print("\n".join(lines[:2]), *(l for l in lines if l.startswith("## ")), sep="\n")


if __name__ == "__main__":
    main()
