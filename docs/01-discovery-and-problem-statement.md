# 1. Discovery and problem statement

The client is modeled on a real furniture manufacturer in Chennai. Its processes, cost structure and pain points
come from requirement interviews with the owner's family. All names, prices and quantities in this repository
are synthetic.

## The business

| Area | As-is |
|---|---|
| Products | Wardrobes (2-, 3- and 4-door), storage cots (~10 designs), pooja mandirs (~12 designs), office tables, custom 8 ft walk-in wardrobes. Offcuts are now made into photo frames, wall clocks and children's ride-on scooters |
| Factory | ~10,000 sq ft rented shed, ~30 monthly-salaried workers, beam saw, panel saw, cold press, CNC router, edge bander |
| Customers | A few retailers across Tamil Nadu, mostly ordering by phone. The largest has 5 company sales staff on its floor who flag low stock. Top 3 retailers make up most sales |
| Suppliers | Laminates, UV boards and plywood in bulk from Gujarat (≈ ₹8,000 freight per load, ~2-week lead time); smaller local buys in Chennai; hardware from Parrys |
| Credit | Suppliers paid after ~30 days; retailers pay every ~2 weeks, with bigger discounts for faster payment |
| Money | Turnover ≈ ₹30 L/month incl. GST; fixed costs ≈ ₹7 L/month (salaries ₹4 L, rent ₹1.5 L, power ₹1 L, other) |
| Pricing | Cost-plus: owner states an 80–90% markup on cost |
| Systems | Excel for daily sales, turnover and who has or hasn't paid; Tally for billing only. No stock records |

## Pain points (owner's words, then what they cost)

| # | Pain point | Why it matters |
|---|---|---|
| P1 | "I want to see what sold each day and what profit it made." | Markup on material cost is not net profit. A rough check (₹30 L turnover incl. GST, 80–90% markup, ₹7 L fixed) puts net profit near **17–20% of revenue**, before any of the leaks below |
| P2 | Sheets run out; some days there is no production | Bulk orders are placed only after retailers pay, so a late payment becomes a stockout. Salaries (≈ ₹15,000/day) are still paid on idle days |
| P3 | Custom walk-in wardrobes: "this is not what I asked" | Retailer staff take the brief, factory staff build it, and nobody holds a signed specification. A notebook signature was tried and didn't settle disputes |
| P4 | Quality escapes: crooked handle holes, paint dispatched wet, peeling edge banding | Rework, plus full free replacement when damage shows up at the retailer |
| P5 | Nothing is counted | Hardware goes missing, screws are wasted, and machines and parts have been stolen |
| P6 | No supervisor; daily work follows the nearest deadline | ~15% of custom deliveries are late |
| P7 | The new in-house cold press: "not convinced it saves money" | Nobody can compare pressing in-house with buying finished UV board per sheet |
| P8 | Machine breakdowns every ~2 months, ≈ ₹10,000 per repair visit | Lost output is not measured |
| P9 | Offcuts are piling up | Now sold as frames, clocks and scooters, but the value recovered isn't tracked |

## Goal

> **Path to ₹40 L/month:** grow turnover from ≈ ₹30 L to ₹40 L (+33% output) by making visible, then stopping,
> the places where money and production time leak in a 30-person furniture factory.

## Design constraint

The accountant enters all data, barely uses Tally, and is wary of new tools. The system therefore has to:
need little data entry, give each role only the screens it needs, run alongside Tally at first, and roll out in phases.
See [05-cutover-and-adoption.md](05-cutover-and-adoption.md).
