"""Write data/master_data.xlsx from the synthetic seed values.

The workbook is the implementation's input template: one sheet per master, plain columns,
an instruction row per sheet. For go-live, the factory's accountant replaces these rows with
real values and setup_erp.py is re-run. Nothing else in the system changes.
"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import seed_catalog as S

OUT = Path(__file__).resolve().parent.parent / "data" / "master_data.xlsx"
HEAD = PatternFill("solid", fgColor="1F4E78")
NOTE = PatternFill("solid", fgColor="FFF2CC")

wb = Workbook()
wb.remove(wb.active)


def sheet(name, note, header, rows):
    ws = wb.create_sheet(name)
    ws.append([note])
    ws["A1"].fill = NOTE
    ws["A1"].font = Font(italic=True)
    ws.append(header)
    for c in ws[2]:
        c.fill, c.font = HEAD, Font(bold=True, color="FFFFFF")
    for r in rows:
        ws.append(list(r))
    for i, h in enumerate(header, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r)])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)
    ws.freeze_panes = "A3"


sheet("Company", "Company identity and monthly fixed costs (₹). Salaries/rent/power drive machine hourly rates.",
      ["Key", "Value"],
      [("company", S.COMPANY), ("abbr", S.ABBR), ("state", S.STATE),
       ("monthly_salaries", S.MONTHLY_SALARIES), ("workers", S.WORKERS), ("monthly_rent", S.MONTHLY_RENT),
       ("monthly_power", S.MONTHLY_POWER), ("monthly_other_fixed", S.MONTHLY_OTHER_FIXED),
       ("work_days", S.WORK_DAYS), ("shift_hours", S.SHIFT_HOURS)])

sheet("Workstations", "One row per machine / bay. Shares of the power bill and floor area must each sum to 1.",
      ["Workstation", "Crew", "Power Share", "Floor Share"], S.WORKSTATIONS)

sheet("Operations", "Production steps and the workstation that performs each.",
      ["Operation", "Workstation"], list(S.OPERATIONS.items()))

items = []
for code, name, group, uom, rate in S.RAW_MATERIALS:
    items.append((code, name, "Raw Material", group, uom, rate, ""))
for code, name, group, uom, rate in S.SCRAP_ITEMS:
    items.append((code, name, "Scrap", group, uom, rate, ""))
for code, name, group, uom in S.SUB_ASSEMBLIES:
    items.append((code, name, "Sub Assembly", group, uom, "", ""))
for code, name, group, rate in S.FINISHED_GOODS:
    items.append((code, name, "Finished Good", group, "Nos", "", rate))
for code, name, group, rate in S.SCRAP_PRODUCTS:
    items.append((code, name, "Scrap Product", group, "Nos", "", rate))
sheet("Items", "Buy Rate = last purchase rate ex-GST (raw/scrap). Sell Rate = list price to retailers ex-GST.",
      ["Item Code", "Item Name", "Type", "Item Group", "UOM", "Buy Rate", "Sell Rate"], items)

bom_rows = []
for item, variants in S.BOMS.items():
    for v in variants:
        for code, qty in v["items"]:
            bom_rows.append((item, v["variant"], "Material", code, qty))
        for op, hrs in v["ops"]:
            bom_rows.append((item, v["variant"], "Operation", op, hrs))
        for code, qty in v["scrap"]:
            bom_rows.append((item, v["variant"], "Scrap", code, qty))
sheet("BOM Lines", "One line per material (qty per unit), operation (hours per unit) or expected scrap. "
      "First variant listed for an item becomes its default BOM.",
      ["Item", "Variant", "Line Type", "Code", "Qty or Hours"], bom_rows)

sheet("Suppliers", "Suppliers and credit days.", ["Supplier", "City", "State", "Credit Days", "Supplies"], S.SUPPLIERS)
sheet("Retailers", "Retailers, credit days, standing discount % and early-payment discount % (paid within 7 days).",
      ["Retailer", "City", "Credit Days", "Discount %", "Early-Pay Discount %", "Notes"], S.CUSTOMERS)
sheet("Warehouses", "Stores inside the factory.", ["Warehouse"], [(w,) for w in S.WAREHOUSES])

OUT.parent.mkdir(parents=True, exist_ok=True)
wb.save(OUT)
print("wrote", OUT)
