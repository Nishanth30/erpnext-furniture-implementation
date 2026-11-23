# 4. Data migration

## Sources (what the accountant keeps today)

| File | Content | Defects deliberately present (typical of small-business Excel) |
|---|---|---|
| `Item_List.xlsx` | Materials as typed over the years | Several names for one item ("18mm prelam PB", "PB 18 prelam", "Prelam board 18"); units spelled `nos`/`pcs`/`sheet`; `Rs.` typed into number cells |
| `Party_List.xlsx` | Suppliers and retailers | A duplicate retailer differing only in case/spacing; a missing GSTIN; a malformed GSTIN |
| `Stock_Count_Sep30.xlsx` | Physical count for go-live | A negative quantity (typo); an item not in the master |
| `Outstanding_Sep30.xlsx` | Open receivables and payables (GST-inclusive) | A receivable from a party that isn't in the party list |
| `Daily_Sales_Sep.xlsx` | September sales register | Baseline only. History is not migrated as transactions (standard practice: migrate open items, not history) |

## Pipeline (`scripts/migrate.py`)

```
read → normalise (whitespace, case, 'Rs.' and commas, dd-mm-yyyy dates)
     → map legacy item names to new item codes (data/item_alias_map.csv, agreed with the accountant)
     → validate (GSTIN pattern, duplicates, unknown items/parties, negative quantities)
     → load valid rows:  party GSTIN/phone · Opening Stock reconciliation · opening Sales/Purchase Invoices
     → everything else → out/migration_exceptions.csv  (file, row, field, value, reason, action)
```
- **Nothing is dropped silently.** Each rejected or corrected row carries a reason and an owner action.
- **Idempotent.** Re-running doesn't duplicate opening documents, so exceptions can be fixed in the source and the script re-run.
- **Opening balances** go to the *Temporary Opening* account; it must net to zero against stock + receivables − payables.

## Result

See `out/migration_summary.md` and `out/migration_exceptions.csv`.

| Load | Outcome |
|---|---|
| Items | 33 legacy names → 27 clean item codes (all mapped); text-in-number rates cleaned |
| Parties | 11 updated with GSTIN/phone; 1 duplicate merged; 2 GSTIN problems routed to the accountant |
| Opening stock | 27 lines loaded (₹8.08 L); 2 rejected (negative count, unknown item) |
| Opening dues | 8 receivables (₹18.23 L) and 6 payables (₹16.37 L) loaded; 1 unknown-party receivable held back |

## Reconciliation (`scripts/reconcile.py`, SQL on the ERPNext database)

| Check | Method | Result |
|---|---|---|
| Opening stock qty and value per item | cleaned count sheet vs Stock Ledger Entries of the opening reconciliation | **PASS 27/27** |
| Receivables per retailer | cleaned dues sheet vs GL balance on *Debtors* | **PASS 5/5** |
| Payables per supplier | cleaned dues sheet vs GL balance on *Creditors* | **PASS 6/6** |
| Temporary Opening | credit balance = stock + receivables − payables | **PASS** |

Full detail: `out/reconciliation_report.md`.
