"""Generate the synthetic 'legacy Excel' files the factory's accountant keeps today.

The files are deliberately messy in the ways real small-business spreadsheets are:
inconsistent item names, mixed units, 'Rs.' in number cells, duplicate parties with
spacing/case differences, missing or malformed GSTINs, a negative stock row, and an
outstanding row for a party that is not in the party list. migrate.py has to clean,
validate and reconcile all of it.

Output: data/legacy/*.xlsx  (deterministic: fixed random seed)
"""
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

import seed_catalog as C

random.seed(42)
OUT = Path(__file__).resolve().parent.parent / "data" / "legacy"
OUT.mkdir(parents=True, exist_ok=True)

STATE_CODE = {"Tamil Nadu": "33", "Gujarat": "24"}
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def fake_gstin(state):
    pan = "".join(random.choice(LETTERS) for _ in range(5)) + f"{random.randint(0, 9999):04d}" + random.choice(LETTERS)
    return f"{STATE_CODE[state]}{pan}1Z{random.choice('0123456789ABCDEFGHIJ')}"


def save(name, header, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(OUT / name)
    print(f"{name}: {len(rows)} rows")


# ---------------------------------------------------------------- item list (as typed by the accountant)
messy_names = {
    "PB-18-PRELAM": ["18mm prelam PB", "PB 18 prelam", "Prelam board 18"],
    "PB-18-RAW": ["18mm plain particle board"],
    "PLY-18-COM": ["18mm comm ply", "Ply 18mm"],
    "PLY-06-BACK": ["6mm back ply"],
    "BRD-UV-18": ["UV sheet 18mm", "UV board"],
    "LAM-1MM-MARBLE": ["Marble lam 1mm", "1mm marble laminate"],
    "LAM-1MM-MATTE": ["Matt lam 1mm"],
    "LAM-08-LINER": ["Liner 0.8"],
    "PNL-GLOSS-PRINT": ["Gloss marble print sheet"],
    "EDGE-PVC-2MM": ["Edge band 2mm"],
    "ADH-PVA": ["Fevicol / PVA"],
    "HW-HINGE-SC": ["Soft close hinge", "hinges soft close"],
    "HW-HANDLE": ["Handle"],
    "HW-LOCK": ["Lock"],
    "HW-CHANNEL": ["Drawer channel"],
    "HW-ROD-SET": ["Hanger rod"],
    "HW-SCREW-BOX": ["Screw box"],
    "HW-MINIFIX": ["Minifix"],
    "HW-CASTOR": ["Castor wheel"],
    "HW-CLOCK-MECH": ["Clock machine"],
    "HW-TUBE-BRKT": ["Tube bracket"],
    "MS-TUBE-25": ["MS pipe 25"],
    "UPH-FOAM-40D": ["Foam 40D"],
    "UPH-FABRIC": ["Sofa cloth"],
    "UPH-BUTTON": ["Button"],
    "PKG-LARGE": ["Bubble wrap set big"],
    "PKG-SMALL": ["Bubble wrap small"],
}
unit_spellings = {"Nos": ["Nos", "nos", "pcs", "sheet"], "Meter": ["mtr", "m", "Meter"], "Kg": ["kg", "Kg"]}

item_rows = []
for code, name, group, uom, rate in C.RAW_MATERIALS:
    for alias in messy_names[code]:
        rate_cell = f"Rs.{rate}" if random.random() < 0.3 else rate
        item_rows.append([alias, group, random.choice(unit_spellings[uom]), rate_cell])
random.shuffle(item_rows)
save("Item_List.xlsx", ["Item", "Category", "Unit", "Last Rate"], item_rows)

# alias -> code mapping the implementer builds with the accountant (cleansing step)
with open(OUT.parent / "item_alias_map.csv", "w") as f:
    f.write("legacy_name,item_code\n")
    for code, aliases in messy_names.items():
        for a in aliases:
            f.write(f"{a},{code}\n")

# ---------------------------------------------------------------- party list
party_rows = []
for name, city, state, days, kind in C.SUPPLIERS:
    party_rows.append(["Supplier", name, city, state, fake_gstin(state), f"00000-{random.randint(100000000, 999999999) % 100000:05d}"])  # phone: obviously fake
for name, city, days, disc, early, note in C.CUSTOMERS:
    party_rows.append(["Retailer", name, city, "Tamil Nadu", fake_gstin("Tamil Nadu"), f"00000-{random.randint(100000000, 999999999) % 100000:05d}"])  # phone: obviously fake
# defects: duplicate with different spacing/case, missing GSTIN, malformed GSTIN
party_rows.append(["Retailer", "MEGA HOME STORES  - PADI", "Chennai", "Tamil Nadu", "", ""])
party_rows[3][4] = ""                                   # supplier missing GSTIN
party_rows[-2][4] = party_rows[-2][4][:-2]              # malformed GSTIN (too short)
save("Party_List.xlsx", ["Type", "Name", "City", "State", "GSTIN", "Phone"], party_rows)

# ---------------------------------------------------------------- go-live stock count (30 Sep 2026)
stock_qty = {
    "PB-18-PRELAM": 120, "PB-18-RAW": 30, "PLY-18-COM": 45, "PLY-06-BACK": 40, "BRD-UV-18": 25,
    "LAM-1MM-MARBLE": 30, "LAM-1MM-MATTE": 35, "LAM-08-LINER": 40, "PNL-GLOSS-PRINT": 12,
    "EDGE-PVC-2MM": 1800, "ADH-PVA": 60, "HW-HINGE-SC": 320, "HW-HANDLE": 110, "HW-LOCK": 45,
    "HW-CHANNEL": 70, "HW-ROD-SET": 30, "HW-SCREW-BOX": 40, "HW-MINIFIX": 1500, "HW-CASTOR": 60,
    "HW-CLOCK-MECH": 25, "HW-TUBE-BRKT": 80, "MS-TUBE-25": 120, "UPH-FOAM-40D": 14, "UPH-FABRIC": 40,
    "UPH-BUTTON": 600, "PKG-LARGE": 60, "PKG-SMALL": 120,
}
stock_rows = []
for code, qty in stock_qty.items():
    alias = messy_names[code][0]
    stock_rows.append([alias, qty, "30-09-2026", "physical count for go-live"])
stock_rows.append(["Liner 0.8", -4, "30-09-2026", "typo in count sheet"])  # defect: negative
stock_rows.append(["Gold handle", 20, "30-09-2026", ""])                     # defect: unknown item
save("Stock_Count_Sep30.xlsx", ["Item", "Qty", "Date", "Remarks"], stock_rows)

# ---------------------------------------------------------------- open dues on 30 Sep 2026 (GST-inclusive amounts)
out_rows = []
inv = 9100
for name, city, days, disc, early, note in C.CUSTOMERS:
    for _ in range(random.randint(1, 3)):
        inv += 1
        d = date(2026, 9, 30) - timedelta(days=random.randint(3, 40 if "late" in note else 20))
        out_rows.append(["Receivable", name, f"KFW/{inv}", d.strftime("%d-%m-%Y"), random.randint(80, 420) * 1000])
bill = 0
for name, city, state, days, kind in C.SUPPLIERS:
    bill += 1
    d = date(2026, 9, 30) - timedelta(days=random.randint(5, 28))
    out_rows.append(["Payable", name, f"SUP-{bill:03d}", d.strftime("%d-%m-%Y"), random.randint(40, 380) * 1000])
out_rows.append(["Receivable", "Velachery Decor", "KFW/9999", "02-08-2026", 64000])  # defect: unknown party
save("Outstanding_Sep30.xlsx", ["Type", "Party", "Bill No", "Bill Date", "Amount (incl GST)"], out_rows)

# ---------------------------------------------------------------- September daily sales register (baseline only)
fg_rates = {c: r for c, _, _, r in C.FINISHED_GOODS if c != "WIW-CUSTOM"}
mix = {"WRD-3D": 12, "WRD-2D": 8, "WRD-4D": 5, "COT-STOR-Q": 10, "COT-STOR-K": 6, "COT-BASIC-Q": 7,
       "MND-3X2": 12, "MND-5FT": 10, "MND-7FT": 5, "OFT-5FT": 10}
sales_rows = []
day = date(2026, 9, 1)
while day.month == 9:
    if day.weekday() != 6:
        for _ in range(random.randint(2, 4)):
            code = random.choices(list(mix), weights=list(mix.values()))[0]
            cust = random.choices([c[0] for c in C.CUSTOMERS], weights=[45, 20, 13, 12, 10])[0]
            qty = random.choices([1, 2, 3], weights=[70, 25, 5])[0]
            amt = round(qty * fg_rates[code] * 1.18)
            sales_rows.append([day.strftime("%d-%m-%Y"), cust, dict((c, n) for c, n, _, _ in C.FINISHED_GOODS)[code], qty, amt, ""])
    day += timedelta(days=1)
save("Daily_Sales_Sep.xlsx", ["Date", "Retailer", "Item", "Qty", "Amount (incl GST)", "Paid?"], sales_rows)
print("total Sep sales incl GST:", sum(r[4] for r in sales_rows))
