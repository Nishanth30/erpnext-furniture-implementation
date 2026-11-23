# 2. Fit-gap analysis (ERPNext v16)

**Standard:** works out of the box. **Configured:** set up through settings and master data, no code.
**Customised:** custom fields, custom DocTypes or a server script, all upgrade-safe (no core code changes).

| Need | Pain point | Fit | How it is met |
|---|---|---|---|
| Daily sales and profit per day | P1 | Configured | Query report *Daily Sales and Profit* on the Owner Dashboard, with cost of goods taken from the valuation at delivery |
| True product cost incl. labour, power, rent | P1 | Configured | Workstations carry hourly wages, electricity and rent from the monthly fixed costs; BOM operations absorb them into product cost |
| Profit per product | P1 | Configured | Query report *Product Profitability* (actual markup on full cost) |
| Retailer-specific prices | — | Configured | Pricing Rule per retailer (standing discount %) |
| Early-payment discount | — | Standard | Payment Terms with a discount window (e.g. 2% if paid within 7 days), posted to its own account |
| Who owes / whom we owe | P2 | Standard | Accounts Receivable / Payable ageing + dashboard cards |
| Sheet reorder before stockout | P2 | Standard | Item reorder levels + automatic Material Requests (Stock Settings: auto-indent) |
| Gujarat freight in material cost | P1 | Standard | Landed Cost Voucher spreads freight across the received sheets |
| GST: intra-state CGST+SGST, inter-state IGST | — | Configured | Tax accounts + Sales/Purchase Taxes templates (full e-invoice / e-way bill / returns: India Compliance app, phase 3) |
| BOMs with operations and scrap | P1, P9 | Standard | Multi-operation BOMs with expected offcut scrap valued into a Scrap Yard warehouse |
| Products made from offcuts | P9 | Standard | Separate BOMs that consume the scrap item, so recovered value is visible |
| Make-or-buy: cold press vs bought board | P7 | Standard | Two active BOMs for the same wardrobe; report *Make or Buy - Door Boards* compares actual cost |
| Signed custom-order specification | P3 | **Customised** | Custom fields on Sales Order (measurements, finish, layout, drawing, signature, spec version) + a *Before Submit* server script that blocks confirmation until every field and the customer's sign-off are present |
| Pre-dispatch quality check | P4 | Standard | Quality Inspection Template "Pre-Dispatch Furniture Check" made mandatory before delivery for all finished goods |
| Transit damage and replacements | P4 | **Customised** | Custom DocType *Transit Damage Log* + Material Issue to a dedicated expense account |
| Stock of hardware, physical counts | P5 | Standard | Separate Hardware Store warehouse, monthly Stock Reconciliation; difference posts to Stock Adjustment |
| Idle days and breakdowns | P2, P8 | **Customised** | Custom DocType *Production Downtime* (reason, hours lost, idle cost, repair cost) |
| Owner sees money, accountant sees entry screens | — | Configured | Role Profiles: Factory Owner, Factory Accountant, Store and Floor, Retail Floor Sales |
| Leak summary in rupees | all | Configured | Query report *Margin Leaks* (SQL over GL, logs and purchase data) |

## Added in phase 2

| Need | Fit | How it is met |
|---|---|---|
| Material planning from the order book | Standard | Production Plan over open Sales Orders → BOM explosion netted against stock → Material Requests ([docs/07](07-production-planning.md)) |
| Reorder points that actually prevent stockouts | Configured | Computed from consumption and lead time; joint replenishment keeps one Gujarat load |
| Approval for large purchases | Configured | Workflow *Purchase Order Approval*: above ₹1 L needs the owner (Purchase Manager) |
| Orders from retailer-floor staff without re-typing | **Integration** | REST order intake service with outbox and idempotency ([docs/09](09-integration-order-intake.md)) |

## Gaps deliberately left for later phases

| Gap | Why not now | Planned |
|---|---|---|
| Work Orders and job cards on the shop floor | Needs a supervisor role that doesn't exist yet; manufacture entries capture cost without it | Phase 3 |
| Tally replacement for statutory filings | The accountant relies on Tally; run both in parallel during the first months | Phase 3, with India Compliance |
| Retail point of sale for the owner's planned showroom | The showroom doesn't exist yet | When it opens (ERPNext POS) |
| SOAP integrations | No SOAP counterpart in this business; the intake pattern (validate → outbox → retry) carries over | If a partner system requires it |
