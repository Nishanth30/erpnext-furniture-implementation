# 3. Solution design

## Process flows as configured

### Procure-to-pay
```
Reorder level hit ──► Material Request ──► Purchase Order ──► Purchase Receipt ──► Purchase Invoice ──► Payment (30 days)
                                                                   │
                                     Gujarat loads only ──► Landed Cost Voucher (freight spread across the sheets)
```
- Taxes: Gujarat suppliers use the IGST 18% template; Tamil Nadu suppliers use CGST 9% + SGST 9%.
- Warehouses: boards, laminates and consumables go to *Raw Material Store*; hardware to *Hardware Store* (counted monthly).

### Make
```
BOM (materials + operations + expected scrap) ──► Manufacture Stock Entry
    consumes materials   ──► from Raw Material / Hardware Store
    absorbs make-cost    ──► wages + power + rent per machine-hour (Additional Costs)
    produces             ──► Finished Goods Store
    produces scrap       ──► Scrap Yard (offcuts, valued)  ──► frames / clocks / scooters BOMs
```
**Costing model.** Every workstation carries hourly costs derived from the monthly fixed costs in the input workbook:

| Component | Formula |
|---|---|
| Wages | crew × ₹4,00,000 ÷ (30 workers × 26 days × 8 h) ≈ crew × ₹64.1 per hour |
| Electricity | station's share of the power bill × ₹1,00,000 ÷ 208 h |
| Rent | station's share of floor area × ₹1,50,000 ÷ 208 h |

Product cost = materials + operating cost − expected scrap value. The rates assume full utilisation. Fixed cost that
production doesn't absorb (idle days, slack) stays visible as a leak instead of quietly disappearing into an average.

**Make-or-buy.** WRD-3D has two active BOMs: one with bought UV door board, one with door board pressed in-house
(sub-assembly *SA-DOOR-PRESSED* on the cold press). Production alternates between them, and the
*Make or Buy - Door Boards* report compares what each wardrobe actually cost.

### Order-to-cash
```
Phone order ──► Sales Order (retailer price rule applied)
             ──► Manufacture (nearest deadline first)
             ──► Delivery Note ──► Quality Inspection (mandatory, 6 checks) ──► submit
             ──► Sales Invoice (GST, payment terms: 14 days, early-payment discount)
             ──► Payment Entry (discount posted to its own account when taken)
Transit damage ──► Transit Damage Log + Material Issue (replacement at cost)
```

### Custom walk-in wardrobe
```
Customer at retailer ──► retailer staff brief ──► factory measures at site
  ──► Sales Order (Order Kind = Custom): width/height/depth, finish, internal layout,
      design drawing, spec version, customer signature, signed date
  ──► Submit  ✖ blocked by server script "Custom Order Sign-off Gate" until all are present
  ──► Submit  ✔ ──► build ──► QC check "Matches order specification" ──► install
```
Any later dispute is settled against the signed, versioned specification attached to the order.

## Customisations (all upgrade-safe)

| Object | Type | Purpose |
|---|---|---|
| 13 fields on Sales Order (`custom_*`), in their own section | Custom Field | Custom-order specification and sign-off |
| Custom Order Sign-off Gate | Server Script (Before Submit) | Refuses to confirm an incomplete or unsigned custom order |
| Transit Damage Log | Custom DocType | Cause, action and replacement cost per damaged unit |
| Production Downtime | Custom DocType | Idle hours, idle cost and repair cost by reason |
| 4 Query Reports, 5 Number Cards, 2 Charts, Owner Dashboard | Report / Dashboard | Owner's view |

## Roles

| Role profile | Sees / does |
|---|---|
| Factory Owner | Everything, including the Owner Dashboard and profit reports |
| Factory Accountant | Sales, purchase and stock entry; invoices and payments |
| Store and Floor | Stock movements, manufacture entries, quality inspections |
| Retail Floor Sales | Sales Orders only (custom-order specs at the retailer's floor) |

## Single source of truth for master data

`data/master_data.xlsx` holds company costs, workstations, operations, items, BOM lines, suppliers, retailers and
warehouses. `setup_erp.py` builds or updates ERPNext from it. Going live means replacing the synthetic rows
with real ones and re-running the script. No code changes.
