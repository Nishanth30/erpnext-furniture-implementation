# 8. User-acceptance test cases

Executed by `scripts/uat.py` against the configured site. **18/18 PASS.**
Evidence column quotes the documents the test created or checked.

> Note: UAT-15 ran before a ninth intake test (crash between saving a draft order and submitting it) was added. The intake suite now has 9 tests, all passing; see [docs/09](09-integration-order-intake.md).

| ID | Process | Steps | Expected | Actual / evidence | Result |
|---|---|---|---|---|---|
| UAT-01 | Procure-to-pay (local) | PO 10 pre-lam boards from a Chennai supplier → receipt → bill | Stock +10; bill carries CGST 9% + SGST 9%; payable = bill total | PUR-ORD-2026-00012 → MAT-PRE-2026-00011 → ACC-PINV-2026-00017; stock 238→248; taxes [('Input CGST', 9.0), ('Input SGST', 9.0)]; payable ₹17,110 | PASS |
| UAT-02 | Procure-to-pay (inter-state) | PO 5 UV boards from the Gujarat wholesaler → receipt → bill | Bill carries IGST 18% only | ACC-PINV-2026-00018: taxes [('Input IGST', 18.0)] | PASS |
| UAT-03 | Landed cost | Landed Cost Voucher: ₹1,000 lorry freight on the UAT-02 receipt | Valuation of each of the 5 boards rises by ₹200 | incoming rate per board after freight: ₹4,500.00 (was ₹4,300.00) | PASS |
| UAT-04 | MRP | Production Plan over November's open orders | Requirements netted against stock; material requests raised only for shortfalls | MFG-PP-2026-00001: 6 items short → 5 material requests; 15 items fully covered by stock | PASS |
| UAT-05 | Manufacture + costing | Manufacture 1 two-door wardrobe from its default BOM | Finished-good value = materials + machine-hour cost − scrap, within 3% of the BOM cost | MAT-STE-2026-00128: wardrobe valued ₹14,807 vs BOM ₹14,413 (2.7% apart, from moving-average material prices) | PASS |
| UAT-06 | Scrap recovery | Same manufacture entry | 9 sq ft of offcuts land in the Scrap Yard at their scrap value | 9 SCRAP-BOARD-OFFCUT → Scrap Yard - KFW @ ₹11 | PASS |
| UAT-07 | Retailer pricing | Sales Order for Mega Home Stores: 1 two-door wardrobe | Retailer's 10% standing discount applied automatically: ₹31,000 → ₹27,900 | SAL-ORD-2026-00083: list ₹31,000, discount 10.0%, rate ₹27,900 | PASS |
| UAT-08 | Pre-dispatch QC gate | Delivery Note for UAT-07 submitted without a quality inspection | Submission refused | refused: Row #1: Quality Inspection is required for Item <strong>WRD-2D</strong> | PASS |
| UAT-09 | Order-to-cash | Record an accepted 6-point inspection, submit the delivery, invoice it | Delivery submits; invoice carries CGST 9% + SGST 9%; due in 14 days | MAT-QA-2026-00094 accepted → MAT-DN-2026-00083 → ACC-SINV-2026-00091 ₹32,922 due 2026-11-17 | PASS |
| UAT-10 | Early-payment discount | Retailer pays the UAT-09 invoice within 7 days | 2% discount posted to 'Early Payment Discount Allowed'; invoice fully settled | ACC-PAY-2026-00056: discount ₹658.44 posted; outstanding now ₹0 | PASS |
| UAT-11 | Custom-order sign-off gate | Confirm a walk-in wardrobe order with no layout, drawing or signature | Confirmation refused, listing what is missing | internal layout, design drawing, customer sign-off | PASS |
| UAT-12 | Custom-order sign-off gate | Same order with full specification, drawing and customer signature | Order confirmed | SAL-ORD-2026-00084 confirmed with signed spec v1 | PASS |
| UAT-13 | Transit damage | Log a damaged delivery and issue a free replacement | Damage logged; replacement cost charged to 'Transit Damage Replacements' | DMG-00011 + MAT-STE-2026-00130: ₹6,550 charged for OFT-5FT | PASS |
| UAT-14 | Roles | Check what the Retail Floor Sales and Accountant profiles can do | Floor sales cannot see purchase bills; the accountant cannot approve purchase orders | Sales User perms on Purchase Invoice: 0; accountant roles ∩ approver roles: none | PASS |
| UAT-15 | Order-intake integration | Run the intake service's integration test suite against this site | Orders from floor staff create confirmed Sales Orders exactly once, including through an outage | pytest: 8 passed, 1 warning in 0.46s | PASS |
| UAT-16 | Purchase approval | PO for 100 pre-lam boards (≈ ₹1.7 L) through the approval workflow | Stays unsubmitted in 'Pending Approval' until the owner approves; then submitted | PUR-ORD-2026-00015 ₹171,100: direct approval refused → Pending Approval (draft) → Approved (submitted) | PASS |
| UAT-17 | Reporting | Daily Sales and Profit report for October vs the general ledger | Report's net sales equal October sales income in the ledger | report ₹2,549,034 vs ledger ₹2,549,034 | PASS |
| UAT-18 | Migration | Opening balances reconciliation (reconcile.py) | All checks PASS | Opening stock: PASS (27/27 match) · Opening receivables (GL: Debtors - KFW): PASS (5/5 match) · Opening payables (GL: Creditors - KFW): PASS (6/6 match) · Temporary Opening balance: PASS | PASS |
