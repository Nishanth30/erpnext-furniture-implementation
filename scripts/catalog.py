"""Load the factory's master data from data/master_data.xlsx (the implementation input template).

Exposes the same names the rest of the scripts use. To go live with real values, edit the
workbook and re-run setup_erp.py; no code changes.
"""
from pathlib import Path

from openpyxl import load_workbook

WB_PATH = Path(__file__).resolve().parent.parent / "data" / "master_data.xlsx"
_wb = load_workbook(WB_PATH, data_only=True)


def _rows(name):
    ws = _wb[name]
    rows = list(ws.iter_rows(min_row=3, values_only=True))
    return [r for r in rows if r and r[0] not in (None, "")]


_company = {k: v for k, v in _rows("Company")}
COMPANY = _company["company"]
ABBR = _company["abbr"]
STATE = _company["state"]
MONTHLY_SALARIES = float(_company["monthly_salaries"])
WORKERS = int(_company["workers"])
MONTHLY_RENT = float(_company["monthly_rent"])
MONTHLY_POWER = float(_company["monthly_power"])
MONTHLY_OTHER_FIXED = float(_company["monthly_other_fixed"])
WORK_DAYS = int(_company["work_days"])
SHIFT_HOURS = int(_company["shift_hours"])
STATION_HOURS = WORK_DAYS * SHIFT_HOURS
LABOUR_RATE = MONTHLY_SALARIES / (WORKERS * STATION_HOURS)
MONTHLY_FIXED = MONTHLY_SALARIES + MONTHLY_RENT + MONTHLY_POWER + MONTHLY_OTHER_FIXED
DAILY_FIXED = MONTHLY_FIXED / WORK_DAYS

WORKSTATIONS = [(n, int(c), float(p), float(f)) for n, c, p, f in _rows("Workstations")]
assert abs(sum(w[2] for w in WORKSTATIONS) - 1) < 1e-6, "Workstations: power shares must sum to 1"
assert abs(sum(w[3] for w in WORKSTATIONS) - 1) < 1e-6, "Workstations: floor shares must sum to 1"


def station_rates(crew, power_share, floor_share):
    """Hourly absorption rates for a workstation, assuming full utilisation."""
    return {
        "hour_rate_labour": round(crew * LABOUR_RATE, 2),
        "hour_rate_electricity": round(power_share * MONTHLY_POWER / STATION_HOURS, 2),
        "hour_rate_rent": round(floor_share * MONTHLY_RENT / STATION_HOURS, 2),
    }


OPERATIONS = {op: ws for op, ws in _rows("Operations")}

RAW_MATERIALS, SCRAP_ITEMS, SUB_ASSEMBLIES, FINISHED_GOODS, SCRAP_PRODUCTS = [], [], [], [], []
for code, name, typ, group, uom, buy, sell in _rows("Items"):
    if typ == "Raw Material":
        RAW_MATERIALS.append((code, name, group, uom, float(buy)))
    elif typ == "Scrap":
        SCRAP_ITEMS.append((code, name, group, uom, float(buy)))
    elif typ == "Sub Assembly":
        SUB_ASSEMBLIES.append((code, name, group, uom))
    elif typ == "Finished Good":
        FINISHED_GOODS.append((code, name, group, float(sell)))
    elif typ == "Scrap Product":
        SCRAP_PRODUCTS.append((code, name, group, float(sell)))
    else:
        raise ValueError(f"Items: unknown type {typ!r} for {code}")

BOMS = {}
for item, variant, kind, code, qty in _rows("BOM Lines"):
    variants = BOMS.setdefault(item, [])
    v = next((x for x in variants if x["variant"] == variant), None)
    if v is None:
        v = {"variant": variant, "items": [], "ops": [], "scrap": []}
        variants.append(v)
    {"Material": v["items"], "Operation": v["ops"], "Scrap": v["scrap"]}[kind].append((code, float(qty)))

SUPPLIERS = [(n, c, s, int(d), k) for n, c, s, d, k in _rows("Suppliers")]
CUSTOMERS = [(n, c, int(d), float(disc), float(e), notes or "") for n, c, d, disc, e, notes in _rows("Retailers")]
WAREHOUSES = [r[0] for r in _rows("Warehouses")]

ITEM_NAME = {c: n for c, n, *_ in RAW_MATERIALS + SCRAP_ITEMS + SUB_ASSEMBLIES}
ITEM_NAME.update({c: n for c, n, _, _ in FINISHED_GOODS + SCRAP_PRODUCTS})
