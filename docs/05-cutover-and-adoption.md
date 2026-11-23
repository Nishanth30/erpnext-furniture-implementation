# 5. Cutover and adoption plan

## Phased rollout

| Phase | Scope | Users | Exit criterion |
|---|---|---|---|
| 1. Visibility (weeks 1–4) | Sales invoices, payments, purchase bills, sheet stock, Owner Dashboard | Accountant enters; owner views | Daily sales and profit match the bank and Excel for 2 consecutive weeks |
| 2. Control (weeks 5–8) | Reorder levels, pre-dispatch QC, custom-order sign-off, damage and downtime logs, monthly hardware count | + store supervisor, + retailer floor staff for custom orders | No custom order built without a signed spec; first month-end count posted |
| 3. Costing (weeks 9–12) | BOM-based manufacture entries with operating cost, make-or-buy, scrap products; GST e-invoice via India Compliance; Tally retired | + floor | Product profitability report trusted by the owner |

## Cutover checklist (go-live at month end)

1. Freeze item, BOM, party and price master data in `data/master_data.xlsx`; owner signs off.
2. `setup_erp.py` builds the site; spot-check 3 BOM costs against the owner's own estimates.
3. Physical count of sheets, hardware and finished goods on the last working day (two people per store).
4. Export open receivables and payables from Excel/Tally as at the go-live date.
5. `migrate.py` loads them; resolve every row in `out/migration_exceptions.csv` with the accountant.
6. `reconcile.py` must report **PASS** on stock, receivables, payables and the Temporary Opening balance.
7. Owner confirms opening receivables per retailer by phone with the top 3 retailers.
8. Day 1: all new invoices raised in ERPNext; Tally used in parallel for billing until the end of phase 3.

## Adoption: designed for a non-technical accountant

- **Four screens:** Sales Invoice, Payment Entry, Purchase Invoice, Stock Entry. Everything else is hidden by role profile.
- **No re-typing:** documents are created from the previous one (order → delivery → invoice; receipt → bill).
- **The owner reads the dashboard daily.** Data the owner actually looks at gets entered; that's the main lever.
- **Training:** 3 sessions of 45 minutes on the accountant's own real transactions, not demo data. A one-page
  laminated cheat sheet per role.
- **Parallel run:** Excel/Tally continue for the first month so nothing is at risk while trust builds.
- **Hand-holding:** first 2 weeks, review the day's entries with the accountant for 15 minutes each evening.
