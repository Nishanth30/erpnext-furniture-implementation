"""SYNTHETIC SEED values used to generate data/master_data.xlsx (run seed_master_workbook.py).
The workbook, not this file, is the source of truth the system loads.

Structure mirrors a real Chennai furniture manufacturer (products, suppliers, processes,
cost structure). Every name, price and quantity here is synthetic. Prices are approximate
2026 wholesale estimates in INR, ex-GST, and are meant to be replaced with real purchase bills.
"""

COMPANY = "Kaveri Furniture Works"
ABBR = "KFW"
STATE = "Tamil Nadu"

# ---------------------------------------------------------------- cost structure
MONTHLY_SALARIES = 400_000          # 30 workers, monthly salaried
WORKERS = 30
MONTHLY_RENT = 150_000              # 10,000 sq ft rented factory
MONTHLY_POWER = 100_000
MONTHLY_OTHER_FIXED = 50_000        # vehicle, phone, misc (owner estimate: fixed ~= 3 L incl. rent+power)
WORK_DAYS = 26
SHIFT_HOURS = 8
STATION_HOURS = WORK_DAYS * SHIFT_HOURS                       # 208 h / month
LABOUR_RATE = MONTHLY_SALARIES / (WORKERS * STATION_HOURS)    # ~= 64.1 per person-hour

# name, crew size, share of power bill, share of floor (rent)
WORKSTATIONS = [
    ("Beam Saw",       2, 0.25, 0.12),
    ("Panel Saw",      1, 0.08, 0.08),
    ("Cold Press",     2, 0.15, 0.12),
    ("CNC Router",     1, 0.22, 0.10),
    ("Edge Bander",    1, 0.15, 0.08),
    ("Finishing Bay",  2, 0.05, 0.12),
    ("Upholstery Bay", 1, 0.02, 0.06),
    ("Assembly Bay",   3, 0.05, 0.22),
    ("QC and Packing", 1, 0.03, 0.10),
]


def station_rates(crew, power_share, floor_share):
    """Hourly absorption rates for an ERPNext Workstation, assuming full utilisation."""
    return {
        "hour_rate_labour": round(crew * LABOUR_RATE, 2),
        "hour_rate_electricity": round(power_share * MONTHLY_POWER / STATION_HOURS, 2),
        "hour_rate_rent": round(floor_share * MONTHLY_RENT / STATION_HOURS, 2),
    }


# operation name -> workstation
OPERATIONS = {
    "Cutting": "Beam Saw",
    "Trim Cutting": "Panel Saw",
    "Laminate Pressing": "Cold Press",
    "CNC Routing": "CNC Router",
    "Edge Banding": "Edge Bander",
    "Finishing": "Finishing Bay",
    "Upholstery": "Upholstery Bay",
    "Assembly": "Assembly Bay",
    "QC and Packing": "QC and Packing",
}

# ---------------------------------------------------------------- items
# code, name, item group, stock uom, valuation/buy rate (ex-GST), is_stock
RAW_MATERIALS = [
    ("PB-18-PRELAM", "Particle Board 18mm Pre-laminated 8x4", "Boards", "Nos", 1450),
    ("PB-18-RAW", "Particle Board 18mm Plain 8x4", "Boards", "Nos", 950),
    ("PLY-18-COM", "Commercial Plywood 18mm 8x4", "Boards", "Nos", 2250),
    ("PLY-06-BACK", "Plywood 6mm Back Panel 8x4", "Boards", "Nos", 950),
    ("BRD-UV-18", "UV High-Gloss Door Board 18mm 8x4 (bought finished)", "Boards", "Nos", 4300),
    ("LAM-1MM-MARBLE", "Decorative Laminate 1mm Marble Finish 8x4", "Laminates", "Nos", 1250),
    ("LAM-1MM-MATTE", "Decorative Laminate 1mm Matte Finish 8x4", "Laminates", "Nos", 1050),
    ("LAM-08-LINER", "Liner Laminate 0.8mm 8x4", "Laminates", "Nos", 480),
    ("PNL-GLOSS-PRINT", "High-Gloss Marble Print Panel 8x4", "Laminates", "Nos", 1600),
    ("EDGE-PVC-2MM", "PVC Edge Band Tape 2mm", "Consumables", "Meter", 10),
    ("ADH-PVA", "PVA Adhesive (pressing)", "Consumables", "Kg", 210),
    ("HW-HINGE-SC", "Soft-Close Hinge", "Hardware", "Nos", 150),
    ("HW-HANDLE", "Wardrobe Handle", "Hardware", "Nos", 220),
    ("HW-LOCK", "Wardrobe Lock", "Hardware", "Nos", 280),
    ("HW-CHANNEL", "Drawer Channel (pair)", "Hardware", "Nos", 340),
    ("HW-ROD-SET", "Hanging Rod Set", "Hardware", "Nos", 240),
    ("HW-SCREW-BOX", "Screws Box (100 pcs)", "Hardware", "Nos", 180),
    ("HW-MINIFIX", "Minifix Connector", "Hardware", "Nos", 6),
    ("HW-CASTOR", "Castor Wheel", "Hardware", "Nos", 45),
    ("HW-CLOCK-MECH", "Wall Clock Mechanism", "Hardware", "Nos", 120),
    ("HW-TUBE-BRKT", "Support Tube Bracket", "Hardware", "Nos", 60),
    ("MS-TUBE-25", "MS Square Tube 25mm", "Hardware", "Meter", 95),
    ("UPH-FOAM-40D", "PU Foam 40D Sheet", "Upholstery", "Nos", 850),
    ("UPH-FABRIC", "Upholstery Fabric", "Upholstery", "Meter", 320),
    ("UPH-BUTTON", "Upholstery Button", "Upholstery", "Nos", 4),
    ("PKG-LARGE", "Packing Set - Large (bubble wrap, corners)", "Consumables", "Nos", 450),
    ("PKG-SMALL", "Packing Set - Small", "Consumables", "Nos", 60),
]

# Scrap: board offcuts recovered from cutting, valued per sq ft.
SCRAP_ITEMS = [
    ("SCRAP-BOARD-OFFCUT", "Board Offcut (usable scrap)", "Scrap", "Square Foot", 11),
]

# Sub-assembly made in-house on the cold press (make-or-buy alternative to BRD-UV-18)
SUB_ASSEMBLIES = [
    ("SA-DOOR-PRESSED", "Door Board 18mm Pressed In-house (ply + marble laminate)", "Sub Assemblies", "Nos"),
]

# code, name, group, selling rate ex-GST to retailers (list price before retailer discount)
FINISHED_GOODS = [
    ("WRD-2D", "Wardrobe 2-Door", "Wardrobes", 31000),
    ("WRD-3D", "Wardrobe 3-Door", "Wardrobes", 39550),
    ("WRD-4D", "Wardrobe 4-Door", "Wardrobes", 49000),
    ("COT-STOR-Q", "Storage Cot Queen - Tufted Headboard", "Cots", 28500),
    ("COT-STOR-K", "Storage Cot King - Tufted Headboard", "Cots", 33500),
    ("COT-BASIC-Q", "Cot Queen - Basic", "Cots", 17500),
    ("MND-3X2", "Pooja Mandir 3x2 ft", "Pooja Mandirs", 9500),
    ("MND-5FT", "Pooja Mandir 5 ft", "Pooja Mandirs", 18500),
    ("MND-7FT", "Pooja Mandir 7 ft", "Pooja Mandirs", 32000),
    ("OFT-5FT", "Office Table 5 ft", "Office Tables", 14000),
    ("WIW-CUSTOM", "Custom Walk-in Wardrobe 8 ft (made to order)", "Custom Orders", 125000),
]

# Products made from recovered offcuts
SCRAP_PRODUCTS = [
    ("SP-PHOTO-FRAME", "Photo Frame (from offcuts)", "Scrap Products", 450),
    ("SP-WALL-CLOCK", "Wall Clock (from offcuts)", "Scrap Products", 650),
    ("SP-KIDS-SCOOTER", "Kids Ride-on Scooter (from offcuts)", "Scrap Products", 1400),
]

# ---------------------------------------------------------------- BOMs
# item -> {"items": [(code, qty)], "ops": [(operation, hours)], "scrap": [(code, qty)], "variant": label}
# Quantities include ~15% cutting wastage on sheets.
BOMS = {
    "SA-DOOR-PRESSED": [{
        "variant": "in-house press",
        "items": [("PLY-18-COM", 1), ("LAM-1MM-MARBLE", 1), ("LAM-08-LINER", 1), ("ADH-PVA", 0.8)],
        "ops": [("Laminate Pressing", 0.3)],
        "scrap": [],
    }],
    "WRD-3D": [
        {   # default: doors from bought UV board
            "variant": "buy door board",
            "items": [("PB-18-PRELAM", 4), ("BRD-UV-18", 1.5), ("PLY-06-BACK", 1.5), ("EDGE-PVC-2MM", 70),
                      ("HW-HINGE-SC", 9), ("HW-HANDLE", 3), ("HW-LOCK", 1), ("HW-CHANNEL", 2),
                      ("HW-ROD-SET", 1), ("HW-SCREW-BOX", 1), ("HW-MINIFIX", 24), ("ADH-PVA", 1),
                      ("PKG-LARGE", 1)],
            "ops": [("Cutting", 1.0), ("Edge Banding", 0.75), ("CNC Routing", 0.5),
                    ("Assembly", 2.5), ("QC and Packing", 0.5)],
            "scrap": [("SCRAP-BOARD-OFFCUT", 12)],
        },
        {   # alternative: doors pressed in-house
            "variant": "make door board",
            "items": [("PB-18-PRELAM", 4), ("SA-DOOR-PRESSED", 1.5), ("PLY-06-BACK", 1.5), ("EDGE-PVC-2MM", 70),
                      ("HW-HINGE-SC", 9), ("HW-HANDLE", 3), ("HW-LOCK", 1), ("HW-CHANNEL", 2),
                      ("HW-ROD-SET", 1), ("HW-SCREW-BOX", 1), ("HW-MINIFIX", 24), ("ADH-PVA", 1),
                      ("PKG-LARGE", 1)],
            "ops": [("Cutting", 1.0), ("Edge Banding", 0.75), ("CNC Routing", 0.5),
                    ("Assembly", 2.5), ("QC and Packing", 0.5)],
            "scrap": [("SCRAP-BOARD-OFFCUT", 12)],
        },
    ],
    "WRD-2D": [{
        "variant": "buy door board",
        "items": [("PB-18-PRELAM", 3), ("BRD-UV-18", 1), ("PLY-06-BACK", 1), ("EDGE-PVC-2MM", 50),
                  ("HW-HINGE-SC", 6), ("HW-HANDLE", 2), ("HW-LOCK", 1), ("HW-CHANNEL", 1),
                  ("HW-ROD-SET", 1), ("HW-SCREW-BOX", 1), ("HW-MINIFIX", 18), ("ADH-PVA", 0.8),
                  ("PKG-LARGE", 1)],
        "ops": [("Cutting", 0.8), ("Edge Banding", 0.6), ("CNC Routing", 0.4),
                ("Assembly", 2.0), ("QC and Packing", 0.5)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 9)],
    }],
    "WRD-4D": [{
        "variant": "buy door board",
        "items": [("PB-18-PRELAM", 5), ("BRD-UV-18", 2), ("PLY-06-BACK", 2), ("EDGE-PVC-2MM", 90),
                  ("HW-HINGE-SC", 12), ("HW-HANDLE", 4), ("HW-LOCK", 2), ("HW-CHANNEL", 2),
                  ("HW-ROD-SET", 2), ("HW-SCREW-BOX", 1.5), ("HW-MINIFIX", 30), ("ADH-PVA", 1.2),
                  ("PKG-LARGE", 1)],
        "ops": [("Cutting", 1.2), ("Edge Banding", 0.9), ("CNC Routing", 0.6),
                ("Assembly", 3.0), ("QC and Packing", 0.6)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 15)],
    }],
    "COT-STOR-Q": [{
        "variant": "standard",
        "items": [("PB-18-PRELAM", 4.5), ("PNL-GLOSS-PRINT", 1), ("EDGE-PVC-2MM", 40),
                  ("MS-TUBE-25", 3.2), ("HW-TUBE-BRKT", 4), ("UPH-FOAM-40D", 1),
                  ("UPH-FABRIC", 1.5), ("UPH-BUTTON", 20), ("HW-SCREW-BOX", 1),
                  ("HW-MINIFIX", 20), ("ADH-PVA", 0.6), ("PKG-LARGE", 1)],
        "ops": [("Cutting", 0.9), ("Edge Banding", 0.6), ("CNC Routing", 0.6), ("Upholstery", 1.0),
                ("Assembly", 1.5), ("QC and Packing", 0.5)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 14)],
    }],
    "COT-STOR-K": [{
        "variant": "standard",
        "items": [("PB-18-PRELAM", 5.5), ("PNL-GLOSS-PRINT", 1.2), ("EDGE-PVC-2MM", 46),
                  ("MS-TUBE-25", 3.8), ("HW-TUBE-BRKT", 4), ("UPH-FOAM-40D", 1.2),
                  ("UPH-FABRIC", 1.8), ("UPH-BUTTON", 24), ("HW-SCREW-BOX", 1),
                  ("HW-MINIFIX", 24), ("ADH-PVA", 0.7), ("PKG-LARGE", 1)],
        "ops": [("Cutting", 1.0), ("Edge Banding", 0.7), ("CNC Routing", 0.7), ("Upholstery", 1.2),
                ("Assembly", 1.7), ("QC and Packing", 0.5)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 16)],
    }],
    "COT-BASIC-Q": [{
        "variant": "standard",
        "items": [("PB-18-PRELAM", 4), ("EDGE-PVC-2MM", 30), ("MS-TUBE-25", 3.2), ("HW-TUBE-BRKT", 4),
                  ("HW-SCREW-BOX", 1), ("HW-MINIFIX", 16), ("PKG-LARGE", 1)],
        "ops": [("Cutting", 0.7), ("Edge Banding", 0.5), ("Assembly", 1.2), ("QC and Packing", 0.4)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 12)],
    }],
    "MND-3X2": [{
        "variant": "standard",
        "items": [("PLY-18-COM", 0.8), ("LAM-1MM-MATTE", 0.6), ("EDGE-PVC-2MM", 18),
                  ("HW-SCREW-BOX", 0.5), ("ADH-PVA", 0.3), ("PKG-SMALL", 2)],
        "ops": [("Cutting", 0.4), ("CNC Routing", 0.8), ("Finishing", 1.0), ("Assembly", 0.8),
                ("QC and Packing", 0.3)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 4)],
    }],
    "MND-5FT": [{
        "variant": "standard",
        "items": [("PLY-18-COM", 1.8), ("LAM-1MM-MATTE", 1.2), ("EDGE-PVC-2MM", 35), ("HW-HINGE-SC", 4),
                  ("HW-HANDLE", 2), ("HW-SCREW-BOX", 1), ("ADH-PVA", 0.6), ("PKG-LARGE", 1)],
        "ops": [("Cutting", 0.7), ("CNC Routing", 1.5), ("Finishing", 2.0), ("Assembly", 1.5),
                ("QC and Packing", 0.4)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 8)],
    }],
    "MND-7FT": [{
        "variant": "standard",
        "items": [("PLY-18-COM", 3.2), ("LAM-1MM-MATTE", 2), ("EDGE-PVC-2MM", 55), ("HW-HINGE-SC", 6),
                  ("HW-HANDLE", 2), ("HW-LOCK", 1), ("HW-SCREW-BOX", 1.5), ("ADH-PVA", 1), ("PKG-LARGE", 1)],
        "ops": [("Cutting", 1.0), ("CNC Routing", 2.5), ("Finishing", 3.0), ("Assembly", 2.5),
                ("QC and Packing", 0.6)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 12)],
    }],
    "OFT-5FT": [{
        "variant": "standard",
        "items": [("PB-18-PRELAM", 2.5), ("EDGE-PVC-2MM", 25), ("HW-CHANNEL", 2), ("HW-HANDLE", 2),
                  ("HW-LOCK", 1), ("HW-SCREW-BOX", 0.5), ("HW-MINIFIX", 12), ("PKG-LARGE", 1)],
        "ops": [("Cutting", 0.6), ("Edge Banding", 0.4), ("Assembly", 1.0), ("QC and Packing", 0.3)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 7)],
    }],
    "WIW-CUSTOM": [{
        "variant": "reference 8 ft unit",
        "items": [("PB-18-PRELAM", 12), ("BRD-UV-18", 5), ("PLY-06-BACK", 4), ("EDGE-PVC-2MM", 220),
                  ("HW-HINGE-SC", 20), ("HW-HANDLE", 8), ("HW-LOCK", 2), ("HW-CHANNEL", 6),
                  ("HW-ROD-SET", 3), ("HW-SCREW-BOX", 3), ("HW-MINIFIX", 80), ("ADH-PVA", 3),
                  ("PKG-LARGE", 3)],
        "ops": [("Cutting", 3.0), ("Edge Banding", 2.5), ("CNC Routing", 1.5),
                ("Assembly", 8.0), ("QC and Packing", 1.5)],
        "scrap": [("SCRAP-BOARD-OFFCUT", 30)],
    }],
    "SP-PHOTO-FRAME": [{
        "variant": "from offcuts",
        "items": [("SCRAP-BOARD-OFFCUT", 1.5), ("PKG-SMALL", 1)],
        "ops": [("Trim Cutting", 0.15), ("Edge Banding", 0.1)],
        "scrap": [],
    }],
    "SP-WALL-CLOCK": [{
        "variant": "from offcuts",
        "items": [("SCRAP-BOARD-OFFCUT", 2), ("HW-CLOCK-MECH", 1), ("PKG-SMALL", 1)],
        "ops": [("Trim Cutting", 0.15), ("CNC Routing", 0.2)],
        "scrap": [],
    }],
    "SP-KIDS-SCOOTER": [{
        "variant": "from offcuts",
        "items": [("SCRAP-BOARD-OFFCUT", 6), ("HW-CASTOR", 4), ("HW-SCREW-BOX", 0.2), ("PKG-SMALL", 1)],
        "ops": [("Trim Cutting", 0.3), ("CNC Routing", 0.4), ("Assembly", 0.3)],
        "scrap": [],
    }],
}

# ---------------------------------------------------------------- parties
# name, city, state, gstin-like id (synthetic), payment terms days, kind
SUPPLIERS = [
    ("Sabarmati Laminates Wholesale", "Ahmedabad", "Gujarat", 30, "boards+laminates (bulk)"),
    ("Morbi Board Industries", "Morbi", "Gujarat", 30, "particle board (bulk)"),
    ("Madras Ply and Lam Traders", "Chennai", "Tamil Nadu", 30, "boards+laminates (local)"),
    ("Parrys Hardware Mart", "Chennai", "Tamil Nadu", 30, "hardware"),
    ("Ambattur Steel Tubes", "Chennai", "Tamil Nadu", 30, "MS tube"),
    ("Sowcarpet Upholstery Supplies", "Chennai", "Tamil Nadu", 30, "foam, fabric"),
]

# name, city, credit days, discount % off list, early-pay discount %, notes
CUSTOMERS = [
    ("Mega Home Stores - Padi", "Chennai", 14, 10.0, 2.0, "largest retailer; 5 company sales staff on its floor"),
    ("Southside Furnishings - Pallikaranai", "Chennai", 14, 8.0, 2.0, "porter delivery"),
    ("Kanchi Furniture Mart", "Kanchipuram", 14, 6.0, 1.5, ""),
    ("Kovai Home Centre", "Coimbatore", 14, 6.0, 1.5, "long-distance delivery"),
    ("Anna Nagar Interiors", "Chennai", 14, 5.0, 1.0, "small retailer, pays late"),
]

WAREHOUSES = ["Raw Material Store", "Hardware Store", "Work In Progress", "Finished Goods Store",
              "Scrap Yard", "Rejected and Damaged"]
