"""The agent's tools: typed, validated operations over the ERPNext REST API.

Design rules
  * The model never builds raw API calls. It picks one of these tools; arguments are validated with
    Pydantic before anything reaches ERPNext, and errors come back to the model as data it can act on.
  * Read tools return small, pre-digested JSON, which keeps a 7B model's context short and its answers
    tied to numbers it was actually given.
  * Write tools only create DRAFTS, as an integration user whose ERPNext roles cannot submit, cancel,
    amend or delete anything (see setup_agent.py).
  * Grounding: before anything is written, each argument must trace back to what the user said (or, for
    purchase lines, to a shortage this session computed). A small model that fills in "2-door" when the user
    said "2 wardrobes", or picks one customer for "the Home store", is refused and told to ask the user.
  * Submitting needs (1) the user's message to ask for it, (2) a human approval, and is (3) executed with the
    approver's own ERPNext credentials, so ERPNext itself records and enforces who confirmed it.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import re
from datetime import date, timedelta
from typing import Callable, Literal

from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator

from erp_api import ERPClient, ERPError

COMPANY = "Kaveri Furniture Works"
FG_WAREHOUSE = "Finished Goods Store - KFW"
STORE_WAREHOUSES = ["Raw Material Store - KFW", "Hardware Store - KFW", "Stores - KFW"]   # usable material stock
TAX_TEMPLATE = "GST 18% In-State - KFW"
SELLING_PRICE_LIST = "Standard Selling"
FG_GROUPS = ["Wardrobes", "Cots", "Pooja Mandirs", "Office Tables", "Scrap Products", "Custom Orders"]
MAX_LINE_QTY = 50
MAX_MR_QTY = 10000
MAX_APPROVALS_PER_REQUEST = 1
DEFAULT_LEAD_DAYS = 7

FILLER = {"the", "a", "an", "of", "for", "and", "to", "size", "unit", "units", "nos", "pc", "pcs", "piece", "pieces"}
BUSINESS_WORDS = {"home", "store", "centre", "center", "mart", "interior", "furnishing", "furniture", "trader",
                  "shop", "retailer", "customer", "the"}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
                "fifty": 50, "hundred": 100, "single": 1}
SUBMIT_WORDS = re.compile(r"\b(submit|confirm|finali[sz]e|book (it|the order)|place (the|this) order|send (it|the request)"
                          r"|go ahead and (submit|confirm))\b", re.I)
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


class ToolError(Exception):
    """A business-level refusal or lookup failure, returned to the model as {"error": ...}."""


# ---------------------------------------------------------------------------- argument schemas
class SearchArgs(BaseModel):
    query: str = Field("", description="Words from the user's request to match against names. Empty lists all.")

    @field_validator("query", mode="before")
    @classmethod
    def _blank(cls, v):                      # small models send {} or null for "everything"
        return v if isinstance(v, str) else ""


class ItemSearchArgs(SearchArgs):
    finished_goods_only: bool = Field(True, description="True: only products we sell. False: also raw materials and hardware.")


class ItemCodeArgs(BaseModel):
    item_code: str = Field(..., description="Exact item code copied from a search_items result.")


class BuildArgs(ItemCodeArgs):
    qty: int = Field(..., ge=1, le=500, description="How many units we want to build.")


class CustomerArgs(BaseModel):
    customer: str = Field(..., description="Exact customer name from search_customers.")


class DuesArgs(BaseModel):
    customer: str | None = Field(None, description="Customer name. Omit to rank ALL customers by money owed.")


class OpenOrdersArgs(BaseModel):
    customer: str | None = Field(None, description="Exact customer name, or omit for all customers.")
    item_code: str | None = Field(None, description="Exact item code, or omit for all items.")


class Line(BaseModel):
    item: str = Field(..., validation_alias=AliasChoices("item", "item_code"),
                      description="Item code copied from a search_items result, or the product exactly as the user "
                                  "described it. Never guess a code.")
    qty: float = Field(..., gt=0, description="Quantity in the item's stock unit, as the user gave it.")


class DraftSOArgs(BaseModel):
    customer: str = Field(..., description="Customer name from search_customers, or the user's words if they match one customer.")
    lines: list[Line] = Field(..., min_length=1, max_length=10)
    delivery_date: str | None = Field(None, description="YYYY-MM-DD, only if the user gave a date. Omit otherwise.")
    submit: bool = Field(False, description="True ONLY if the user explicitly asked to submit/confirm the order. "
                                            "A human must still approve.")


class DraftMRArgs(BaseModel):
    lines: list[Line] = Field(..., min_length=1, max_length=15)
    reason: str = Field("Raised by the operations agent", description="Why we are buying, in a few words.")
    required_by: str | None = Field(None, description="YYYY-MM-DD, only if the user gave a date. Omit otherwise.")
    submit: bool = Field(False, description="True ONLY if the user explicitly asked to submit/send it. A human must still approve.")


class SubmitArgs(BaseModel):
    doctype: Literal["Sales Order", "Material Request"]
    name: str = Field(..., description="Document name copied from a create_draft_* result.")


# ---------------------------------------------------------------------------- text helpers
def normalize(text: str) -> str:
    """Lower-case; number words to digits; join a number with its unit so "2 wardrobes" (a quantity) differs
    from "2-door" / "two door" (a product variant) and "5 ft" == "5ft" == "five feet"."""
    t = text.lower()
    t = re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: str(NUMBER_WORDS[m.group(1)]), t)
    t = re.sub(r"(\d+)\s*-?\s*(doors?|door)\b", r"\1door", t)
    t = re.sub(r"(\d+)\s*x\s*(\d+)", r"\1x\2", t)
    t = re.sub(r"(?<![x\d])(\d+)\s*-?\s*(ft|feet|foot)\b\.?", r"\1ft", t)
    return t


def _tokens(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", normalize(text).replace("-", " ")):
        if len(t) > 4 and t.endswith(("xes", "ches", "shes")):
            t = t[:-2]                              # boxes -> box
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]                              # cots -> cot, wardrobes -> wardrobe
        out.add(t)
    return out


def numbers_in(text: str) -> set[float]:
    """Stand-alone numbers in the user's text (not '2door' / '5ft', which are product variants)."""
    return {float(x.replace(",", "")) for x in re.findall(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w])", normalize(text))}


def mentions_date(text: str, iso: str) -> bool:
    d = date.fromisoformat(iso)
    t = text.lower()
    if iso in t or d.strftime("%d-%m-%Y") in t or d.strftime("%d/%m/%Y") in t:
        return True
    month = MONTHS[d.month - 1]
    return (month in t or re.search(rf"\b{month[:3]}\b", t) is not None) and \
        re.search(rf"\b0?{d.day}(st|nd|rd|th)?\b", t) is not None


def _rank(query: str, rows: list[dict], text_of: Callable[[dict], str], limit=5) -> list[dict]:
    q = _tokens(query) - FILLER
    if not q:
        return rows[:limit * 4]
    scored = [(len(q & _tokens(text_of(r))) / len(q), r) for r in rows]
    scored = sorted([s for s in scored if s[0] > 0], key=lambda x: -x[0])
    best = scored[0][0] if scored else 0
    if best >= 0.99:                                # every query word matched: don't pad with partial matches
        return [r for s, r in scored if s >= 0.99][:limit]
    return [r for s, r in scored if s >= best - 1e-9 or s >= 0.5][:limit]


def _full_matches(query, rows, text_of):
    q = _tokens(query) - FILLER
    hits = [r for r in rows if q and q <= _tokens(text_of(r))] if q else []
    if not hits:                                    # "20 boxes of screws": drop bare quantities
        q = {t for t in q if not t.isdigit()}
        hits = [r for r in rows if q and q <= _tokens(text_of(r))] if q else []
    return hits


def _strip_qty(text: str, qty: float) -> str:
    """'2 wardrobes' with qty 2 -> 'wardrobes': the number is the quantity, not part of the product name."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s+(.*)", normalize(text))
    return m.group(2) if m and float(m.group(1)) == qty else text


def _num(x) -> float:
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(s: str | None, default_days: int) -> str:
    if not s:
        return (date.today() + timedelta(days=default_days)).isoformat()
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise ToolError(f"date '{s}' is not YYYY-MM-DD")
    if d < date.today():
        raise ToolError(f"date {d} is in the past; today is {date.today()}")
    return d.isoformat()


def _item_text(i: dict) -> str:
    return f"{i['name']} {i['item_name']}"


# ---------------------------------------------------------------------------- the tool set
class Tool:
    def __init__(self, name, description, args_model, fn, kind):
        self.name, self.description, self.args_model, self.fn, self.kind = name, description, args_model, fn, kind

    def schema(self):
        params = self.args_model.model_json_schema()
        params.pop("title", None)
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": params}}


class ERPTools:
    """Bound to one ERP client and one agent session (the session id prefixes every idempotency key)."""

    def __init__(self, erp: ERPClient, session: str, approve: Callable[[str], bool] | None = None,
                 approver: ERPClient | None = None):
        self.erp, self.session = erp, session
        self.approve = approve or (lambda summary: False)       # default: nothing is submitted without a human
        self._approver = approver
        self._cache: dict[str, object] = {}
        # Grounding context, maintained by the agent loop. None = no grounding (direct use in scripts/tests).
        self.user_text: str | None = None       # every user message in the conversation
        self.user_latest: str | None = None     # the message being answered
        self.shortages: dict[str, float] = {}   # item_code -> short_by, from check_build_capacity this session
        self.approvals_this_request = 0
        self.tools = {t.name: t for t in [
            Tool("search_customers", "Find customers (retailers) by name or area. Returns exact names, payment terms, unpaid invoices and money owed.",
                 SearchArgs, self.search_customers, "read"),
            Tool("search_items", "Find products or materials by name/code. Returns exact item codes, units and selling price.",
                 ItemSearchArgs, self.search_items, "read"),
            Tool("get_stock", "Stock of one item in every warehouse: on hand, reserved for open orders, projected, reorder level.",
                 ItemCodeArgs, self.get_stock, "read"),
            Tool("check_build_capacity", "Can we manufacture N units of a product from the materials in our stores? Lists shortages from its bill of materials.",
                 BuildArgs, self.check_build_capacity, "read"),
            Tool("get_customer_dues", "Unpaid invoices of one customer with due dates and overdue days; omit customer to rank all customers by money owed.",
                 DuesArgs, self.get_customer_dues, "read"),
            Tool("list_open_orders", "Confirmed sales orders not yet fully delivered, optionally for one customer and/or item.",
                 OpenOrdersArgs, self.list_open_orders, "read"),
            Tool("create_draft_sales_order", "Create a DRAFT sales order (not confirmed) for products we sell.",
                 DraftSOArgs, self.create_draft_sales_order, "draft"),
            Tool("create_draft_material_request", "Create a DRAFT purchase request for raw materials or hardware (not finished products).",
                 DraftMRArgs, self.create_draft_material_request, "draft"),
            Tool("submit_document", "Ask the human to approve, then submit a draft THIS agent created. Only if the user asked to submit/confirm.",
                 SubmitArgs, self.submit_document, "submit"),
        ]}

    def schemas(self):
        return [t.schema() for t in self.tools.values()]

    def begin_request(self, user_text: str, user_latest: str):
        self.user_text, self.user_latest = user_text, user_latest
        self.approvals_this_request = 0

    def execute(self, name: str, raw_args) -> tuple[dict, str]:
        """Run a tool call. Returns (result, status): ok | invalid_args | refused | unknown_tool | tool_error."""
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"unknown tool '{name}'. Available: {sorted(self.tools)}"}, "unknown_tool"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            parsed = tool.args_model.model_validate(args)
        except (json.JSONDecodeError, ValidationError) as e:
            detail = e.errors(include_url=False) if isinstance(e, ValidationError) else str(e)
            return {"error": "invalid arguments", "details": json.loads(json.dumps(detail, default=str))[:5]}, "invalid_args"
        try:
            return tool.fn(parsed), "ok"
        except (ToolError, ERPError) as e:
            return {"error": str(e)}, "refused"
        except Exception as e:                  # network timeouts, unexpected data: report, never crash the session
            return {"error": f"tool failed ({type(e).__name__}); try again or tell the user"}, "tool_error"

    # ------------------------------------------------------------ cached master data
    def _customers(self):
        if "customers" not in self._cache:
            self._cache["customers"] = self.erp.list("Customer", filters=[["disabled", "=", 0]],
                                                     fields=["name", "customer_group", "payment_terms", "territory"])
        return self._cache["customers"]

    def _items(self):
        if "items" not in self._cache:
            items = self.erp.list("Item", filters=[["disabled", "=", 0]],
                                  fields=["name", "item_name", "item_group", "stock_uom", "is_purchase_item"])
            prices = {p["item_code"]: p["price_list_rate"] for p in
                      self.erp.list("Item Price", filters=[["price_list", "=", SELLING_PRICE_LIST]],
                                    fields=["item_code", "price_list_rate"])}
            for it in items:
                it["selling_rate"] = prices.get(it["name"])
            self._cache["items"] = {it["name"]: it for it in items}
        return self._cache["items"]

    def _scope_pool(self, scope):
        items = list(self._items().values())
        if scope == "sell":
            return [i for i in items if i["item_group"] in FG_GROUPS]
        if scope == "buy":
            return [i for i in items if i["item_group"] not in FG_GROUPS and i.get("is_purchase_item")]
        return items

    # ------------------------------------------------------------ name resolution
    # Exact name/code wins. Otherwise, within the scope of the tool (products for sales, purchasable materials
    # for purchases), a description that fully matches exactly ONE record resolves to it. Several full matches
    # is a real ambiguity: refuse and list them so the model asks the user.
    def _customer(self, name):
        for c in self._customers():
            if c["name"].lower() == name.strip().lower():
                return c
        full = _full_matches(name, self._customers(), lambda c: f"{c['name']} {c.get('territory') or ''}")
        if len(full) == 1:
            return full[0]
        if len(full) > 1:
            raise ToolError(f"'{name}' matches several customers: {[c['name'] for c in full]}. Ask the user which one.")
        sugg = [c["name"] for c in _rank(name, self._customers(), lambda c: c["name"], 3)]
        raise ToolError(f"no customer named '{name}'. Closest: {sugg or 'none'}. Use search_customers.")

    def _item(self, code, scope=None):
        items = self._items()
        it = items.get(code) or items.get(code.strip().upper())
        if it:
            return it
        pool = self._scope_pool(scope)
        for i in pool:
            if i["item_name"].lower() == code.strip().lower():
                return i
        full = _full_matches(code, pool, _item_text)
        if len(full) == 1:
            return full[0]
        if len(full) > 1:
            raise ToolError(f"'{code}' matches several items: {[i['name'] + ' (' + i['item_name'] + ')' for i in full][:6]}. "
                            "Ask the user which one.")
        sugg = difflib.get_close_matches(code.upper(), [i["name"] for i in pool], n=3, cutoff=0.5) or \
            [i["name"] for i in _rank(code, pool, _item_text, 3)]
        if not sugg:
            raise ToolError(f"no item '{code}': we don't sell or stock it. Use search_items to check.")
        named = "; ".join(f"{c} = {items[c]['item_name']}" for c in sugg)
        raise ToolError(f"'{code}' is not an item code (codes are never guessed). Did you mean: {named}? If exactly one "
                        "of these is what the user asked for, call the tool again with that code now; otherwise ask the user.")

    def _dues(self, customer=None):
        flt = [["docstatus", "=", 1], ["outstanding_amount", ">", 0]]
        if customer:
            flt.append(["customer", "=", customer])
        return self.erp.list("Sales Invoice", filters=flt, order_by="due_date asc", limit=5000,
                             fields=["name", "customer", "posting_date", "due_date", "outstanding_amount"])

    # ------------------------------------------------------------ grounding (write tools only)
    def _grounded_item(self, it: dict, qty: float, scope: str) -> dict:
        """The product and quantity must come from the user's words, or (purchases) from a computed shortage."""
        if self.user_text is None:
            return it
        if scope == "buy" and it["name"] in self.shortages:
            if qty + 1e-9 < self.shortages[it["name"]]:
                raise ToolError(f"{it['name']} is short by {self.shortages[it['name']]}; quantity {qty} would not cover it")
            return it
        said = _tokens(self.user_text)
        mine = _tokens(_item_text(it)) - FILLER
        if not (mine - {t for t in mine if t.isdigit()}) & said:
            raise ToolError(f"the user never mentioned {it['item_name']}. Ask the user which product they mean; create nothing.")
        siblings = [s for s in self._scope_pool(scope) if s["item_group"] == it["item_group"] and s["name"] != it["name"]]
        for s in siblings:
            if not (mine - _tokens(_item_text(s))) & said:
                raise ToolError(f"the user's words don't tell {it['item_name']} apart from {s['item_name']}. "
                                "Ask the user which one they mean; create nothing.")
        if qty not in numbers_in(self.user_text):
            raise ToolError(f"the user did not give quantity {qty:g} for {it['item_name']}. Ask the user; create nothing.")
        return it

    def _grounded_customer(self, c: dict) -> dict:
        if self.user_text is None:
            return c
        def distinctive(cust):
            return _tokens(cust["name"]) - BUSINESS_WORDS
        latest = _tokens(self.user_latest or "")
        if distinctive(c) & latest:
            return c
        named_now = [x["name"] for x in self._customers() if distinctive(x) & latest]
        if named_now or latest & BUSINESS_WORDS:
            raise ToolError(f"this request does not name '{c['name']}'. Ask the user which customer they mean; create nothing.")
        earlier = [x for x in self._customers() if distinctive(x) & _tokens(self.user_text)]
        if [x["name"] for x in earlier] == [c["name"]]:          # e.g. "how much does Kovai owe?" ... "draft them 2 tables"
            return c
        raise ToolError(f"the user has not named a customer for this order. Ask which customer; create nothing.")

    def _grounded_date(self, iso: str | None, what: str):
        if iso and self.user_text is not None and not mentions_date(self.user_text, iso):
            raise ToolError(f"the user did not give a {what}; omit it to use the standard lead time")

    def _gate_submit(self):
        if self.user_latest is not None and not SUBMIT_WORDS.search(self.user_latest):
            raise ToolError("the user did not ask to submit or confirm; leave it as a draft")
        if self.approvals_this_request >= MAX_APPROVALS_PER_REQUEST:
            raise ToolError("an approval was already requested for this message; answer the user")

    # ------------------------------------------------------------ read tools
    def search_customers(self, a: SearchArgs):
        rows = _rank(a.query, self._customers(), lambda c: f"{c['name']} {c.get('territory') or ''}")
        if not rows:
            raise ToolError(f"no customer matches '{a.query}'. Customers: {[c['name'] for c in self._customers()]}")
        dues = self._dues()
        today = date.today()
        out = []
        for c in rows:
            mine = [d for d in dues if d["customer"] == c["name"]]
            overdue = [d for d in mine if d.get("due_date") and date.fromisoformat(d["due_date"]) < today]
            out.append({"customer": c["name"], "payment_terms": c.get("payment_terms"),
                        "unpaid_invoices": len(mine), "outstanding_inr": _num(sum(d["outstanding_amount"] for d in mine)),
                        "overdue_inr": _num(sum(d["outstanding_amount"] for d in overdue))})
        if not a.query.strip():
            out.sort(key=lambda r: -r["outstanding_inr"])
            return {"matches_sorted_by_outstanding_desc": out}
        return {"matches": out}

    def search_items(self, a: ItemSearchArgs):
        pool = self._scope_pool("sell" if a.finished_goods_only else None)
        text = (lambda i: f"{i['name']} {i['item_name']} {i['item_group']}")
        rows = _rank(a.query, pool, text, 6)
        note = None
        if a.finished_goods_only and a.query.strip() and not _full_matches(a.query, pool, text):
            alt = _full_matches(a.query, list(self._items().values()), text)   # "wardrobe locks": hardware
            if alt:
                rows = alt[:6]
                note = "no finished product matches; these are materials/hardware we buy, not products we sell"
        if not rows:
            raise ToolError(f"no item matches '{a.query}': we don't sell or stock it. We make wardrobes, cots, "
                            "pooja mandirs, office tables and small offcut products.")
        out = {"matches": [{"item_code": i["name"], "item_name": i["item_name"], "group": i["item_group"],
                            "unit": i["stock_uom"], "selling_rate_inr": _num(i["selling_rate"]) or None} for i in rows]}
        return {**out, "note": note} if note else out

    def get_stock(self, a: ItemCodeArgs):
        it = self._item(a.item_code)
        bins = self.erp.list("Bin", filters=[["item_code", "=", it["name"]]],
                             fields=["warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty"])
        doc = self.erp.get("Item", it["name"])
        reorder = [{"warehouse": r["warehouse"], "reorder_level": _num(r["warehouse_reorder_level"]),
                    "reorder_qty": _num(r["warehouse_reorder_qty"])} for r in doc.get("reorder_levels") or []]
        rows = [{"warehouse": b["warehouse"], "on_hand": _num(b["actual_qty"]), "reserved_for_orders": _num(b["reserved_qty"]),
                 "on_order_from_suppliers": _num(b["ordered_qty"]), "projected": _num(b["projected_qty"])} for b in bins]
        return {"item_code": it["name"], "item_name": it["item_name"], "unit": it["stock_uom"],
                "total_on_hand": _num(sum(r["on_hand"] for r in rows)),
                "total_reserved_for_orders": _num(sum(r["reserved_for_orders"] for r in rows)),
                "warehouses": rows or "no stock records (never stocked)", "reorder_levels": reorder or None}

    def check_build_capacity(self, a: BuildArgs):
        it = self._item(a.item_code)
        boms = self.erp.list("BOM", filters=[["item", "=", it["name"]], ["is_default", "=", 1], ["docstatus", "=", 1]],
                             fields=["name", "quantity"])
        if not boms:
            raise ToolError(f"{it['name']} has no bill of materials; it is bought or not manufactured")
        bom = self.erp.get("BOM", boms[0]["name"])
        per_unit: dict[str, float] = {}
        for row in bom.get("exploded_items") or bom["items"]:     # exploded: sub-assemblies expanded to materials
            per_unit[row["item_code"]] = per_unit.get(row["item_code"], 0) + row["stock_qty"] / (bom["quantity"] or 1)
        per_unit = {k: v for k, v in per_unit.items() if v > 0}
        on_hand: dict[str, float] = {}
        for b in self.erp.list("Bin", filters=[["item_code", "in", list(per_unit)], ["warehouse", "in", STORE_WAREHOUSES]],
                               fields=["item_code", "actual_qty"]):
            on_hand[b["item_code"]] = on_hand.get(b["item_code"], 0) + b["actual_qty"]
        comps, shortages = [], []
        max_build = min((math.floor(on_hand.get(c, 0) / q + 1e-9) for c, q in per_unit.items()), default=0)
        for code, q in per_unit.items():
            need, have = q * a.qty, on_hand.get(code, 0)
            comps.append({"item_code": code, "needed": _num(need), "on_hand": _num(have)})
            if have + 1e-9 < need:
                shortages.append({"item_code": code, "item_name": self._items().get(code, {}).get("item_name", code),
                                  "short_by": _num(need - have), "unit": self._items().get(code, {}).get("stock_uom")})
                self.shortages[code] = max(self.shortages.get(code, 0), _num(need - have))
        return {"item_code": it["name"], "item_name": it["item_name"], "requested_qty": a.qty,
                "can_build_all": not shortages, "max_buildable_now": int(max_build), "shortages": shortages,
                "bom": bom["name"], "components": comps,
                "note": "materials from the exploded BOM vs stock in the raw-material and hardware stores; "
                        "ignores stock already promised to other production"}

    def get_customer_dues(self, a: DuesArgs | CustomerArgs):
        if not a.customer:                                     # ranking across all customers
            ranking = []
            for c in self._customers():
                d = self.get_customer_dues(CustomerArgs(customer=c["name"]))
                ranking.append({k: d[k] for k in ("customer", "unpaid_invoices", "total_outstanding_inr",
                                                  "total_overdue_inr", "oldest_days_overdue")})
            ranking.sort(key=lambda r: -r["total_outstanding_inr"])
            return {"customers_ranked_by_outstanding": ranking,
                    "total_outstanding_all_inr": _num(sum(r["total_outstanding_inr"] for r in ranking))}
        c = self._customer(a.customer)
        today = date.today()
        invs = []
        for d in self._dues(c["name"]):
            late = (today - date.fromisoformat(d["due_date"])).days if d.get("due_date") else 0
            invs.append({"invoice": d["name"], "due_date": d.get("due_date"), "outstanding_inr": _num(d["outstanding_amount"]),
                         "days_overdue": max(late, 0)})
        return {"customer": c["name"], "payment_terms": c.get("payment_terms"), "unpaid_invoices": len(invs),
                "total_outstanding_inr": _num(sum(i["outstanding_inr"] for i in invs)),
                "total_overdue_inr": _num(sum(i["outstanding_inr"] for i in invs if i["days_overdue"] > 0)),
                "oldest_days_overdue": max([i["days_overdue"] for i in invs], default=0), "invoices": invs,
                "note": "from submitted invoices; unallocated advances and credit notes are not netted"}

    def list_open_orders(self, a: OpenOrdersArgs):
        flt = [["docstatus", "=", 1], ["status", "in", ["To Deliver and Bill", "To Deliver"]]]
        if a.customer:
            flt.append(["customer", "=", self._customer(a.customer)["name"]])
        item = self._item(a.item_code)["name"] if a.item_code else None
        orders = []
        for so in self.erp.list("Sales Order", filters=flt, fields=["name", "customer", "delivery_date"],
                                order_by="delivery_date asc", limit=5000):
            doc = self.erp.get("Sales Order", so["name"])
            lines = [{"item_code": r["item_code"], "pending_qty": _num(r["qty"] - r["delivered_qty"])} for r in doc["items"]
                     if r["qty"] - r["delivered_qty"] > 0 and (item is None or r["item_code"] == item)]
            if lines:
                orders.append({"order": so["name"], "customer": so["customer"], "delivery_date": so["delivery_date"], "lines": lines})
        totals: dict[str, float] = {}
        for o in orders:
            for ln in o["lines"]:
                totals[ln["item_code"]] = totals.get(ln["item_code"], 0) + ln["pending_qty"]
        return {"open_orders": len(orders), "pending_qty_by_item": totals, "orders": orders[:15],
                **({"note": f"showing 15 of {len(orders)}"} if len(orders) > 15 else {})}

    # ------------------------------------------------------------ write tools (drafts only)
    def _ref(self, payload: dict) -> str:
        digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
        return f"{self.session}:{digest}"

    def _existing_draft(self, doctype, ref):
        """Same request in this session -> same draft. If the earlier one was already submitted or cancelled,
        a repeat is a genuinely new request: it gets a fresh key."""
        found = self.erp.list(doctype, filters=[["custom_agent_ref", "like", f"{ref}%"]], fields=["name", "docstatus"])
        drafts = [f for f in found if f["docstatus"] == 0]
        return (drafts[0], ref) if drafts else (None, ref if not found else f"{ref}.{len(found)}")

    def _status(self, docstatus):
        return {0: "Draft (not confirmed)", 1: "Submitted", 2: "Cancelled"}.get(docstatus, str(docstatus))

    def create_draft_sales_order(self, a: DraftSOArgs):
        cust = self._grounded_customer(self._customer(a.customer))
        lines = []
        for ln in a.lines:
            name = _strip_qty(ln.item, ln.qty)
            it = self._grounded_item(self._item(name, scope="sell"), ln.qty, "sell")
            if it["item_group"] not in FG_GROUPS:
                raise ToolError(f"{it['name']} ({it['item_name']}) is a material, not a product we sell")
            if ln.qty != int(ln.qty) or ln.qty > MAX_LINE_QTY:
                raise ToolError(f"quantity {ln.qty} for {it['name']} must be a whole number between 1 and {MAX_LINE_QTY}")
            lines.append({"item_code": it["name"], "qty": int(ln.qty)})
        self._grounded_date(a.delivery_date, "delivery date")
        if a.submit:
            self._gate_submit()
        due = _parse_date(a.delivery_date, DEFAULT_LEAD_DAYS)
        existing, ref = self._existing_draft("Sales Order", self._ref({"so": cust["name"], "due": due,
                                                                       "lines": sorted((l["item_code"], l["qty"]) for l in lines)}))
        if existing:
            doc, again = self.erp.get("Sales Order", existing["name"]), True
        else:
            taxes = self.erp.call("erpnext.controllers.accounts_controller.get_taxes_and_charges",
                                  master_doctype="Sales Taxes and Charges Template", master_name=TAX_TEMPLATE)
            doc = self.erp.insert({
                "doctype": "Sales Order", "customer": cust["name"], "company": COMPANY, "order_type": "Sales",
                "transaction_date": date.today().isoformat(), "delivery_date": due, "set_warehouse": FG_WAREHOUSE,
                "custom_order_kind": "Standard", "custom_agent_ref": ref, "taxes_and_charges": TAX_TEMPLATE, "taxes": taxes,
                "items": [{**l, "delivery_date": due, "warehouse": FG_WAREHOUSE} for l in lines]})
            again = False
        out = {"draft_sales_order": doc["name"], "status": self._status(doc["docstatus"]), "already_existed": again,
               "customer": cust["name"], "delivery_date": due,
               "lines": [{"item_code": r["item_code"], "qty": _num(r["qty"]), "rate_inr": _num(r["rate"]),
                          "amount_inr": _num(r["amount"])} for r in doc["items"]],
               "net_total_inr": _num(doc["net_total"]), "grand_total_incl_gst_inr": _num(doc["grand_total"])}
        try:                                    # the draft exists from here on: never lose it to a later error
            dues = self.get_customer_dues(CustomerArgs(customer=cust["name"]))
            if dues["total_overdue_inr"] > 0:
                out["credit_warning"] = (f"{cust['name']} has INR {dues['total_overdue_inr']:,.0f} overdue (oldest "
                                         f"{dues['oldest_days_overdue']} days). Mention this; the approver also sees it.")
            if a.submit:
                out["submission"] = self.submit_document(SubmitArgs(doctype="Sales Order", name=doc["name"]))
        except (ToolError, ERPError) as e:
            out["warning"] = f"draft created, but a follow-up step failed: {e}"
        return out

    def create_draft_material_request(self, a: DraftMRArgs):
        lines = []
        for ln in a.lines:
            name = _strip_qty(ln.item, ln.qty)
            it = self._grounded_item(self._item(name, scope="buy"), ln.qty, "buy")
            if it["item_group"] in FG_GROUPS or not it.get("is_purchase_item"):
                raise ToolError(f"{it['name']} ({it['item_name']}) is something we make, not buy; use check_build_capacity")
            if ln.qty > MAX_MR_QTY:
                raise ToolError(f"quantity {ln.qty} for {it['name']} is above the {MAX_MR_QTY} limit; check with the user")
            wh = next((d.get("default_warehouse") for d in self.erp.get("Item", it["name"]).get("item_defaults") or []
                       if d.get("default_warehouse")), "Raw Material Store - KFW")
            lines.append({"item_code": it["name"], "qty": ln.qty, "warehouse": wh, "uom": it["stock_uom"]})
        self._grounded_date(a.required_by, "required-by date")
        if a.submit:
            self._gate_submit()
        need_by = _parse_date(a.required_by, DEFAULT_LEAD_DAYS)
        existing, ref = self._existing_draft("Material Request",
                                             self._ref({"mr": sorted((l["item_code"], l["qty"]) for l in lines), "by": need_by}))
        if existing:
            doc, again = self.erp.get("Material Request", existing["name"]), True
        else:
            doc = self.erp.insert({
                "doctype": "Material Request", "material_request_type": "Purchase", "company": COMPANY,
                "transaction_date": date.today().isoformat(), "schedule_date": need_by, "custom_agent_ref": ref,
                "items": [{**l, "schedule_date": need_by, "description": a.reason[:140]} for l in lines]})
            again = False
        out = {"draft_material_request": doc["name"], "status": self._status(doc["docstatus"]), "already_existed": again,
               "required_by": need_by, "lines": [{"item_code": r["item_code"], "qty": _num(r["qty"]),
                                                  "warehouse": r["warehouse"]} for r in doc["items"]]}
        if a.submit:
            try:
                out["submission"] = self.submit_document(SubmitArgs(doctype="Material Request", name=doc["name"]))
            except (ToolError, ERPError) as e:
                out["warning"] = f"draft created, but submitting failed: {e}"
        return out

    def _approver_client(self) -> ERPClient:
        if self._approver is None:
            key, secret = os.environ.get("ERP_APPROVER_KEY"), os.environ.get("ERP_APPROVER_SECRET")
            if not key or not secret:
                raise ToolError("no approver is configured, so nothing can be submitted; it stays a draft")
            self._approver = ERPClient(key=key, secret=secret)
        return self._approver

    def submit_document(self, a: SubmitArgs):
        try:
            doc = self.erp.get(a.doctype, a.name)
        except ERPError:
            raise ToolError(f"{a.doctype} {a.name} not found")
        if not str(doc.get("custom_agent_ref") or "").startswith(f"{self.session}:"):
            raise ToolError(f"{a.name} was not drafted by this agent session; only its own drafts can be submitted")
        if doc["docstatus"] != 0:
            return {"document": a.name, "submitted": doc["docstatus"] == 1, "note": f"already {self._status(doc['docstatus'])}"}
        self._gate_submit()
        lines = ", ".join(f"{_num(r['qty']):g} x {r['item_code']}" for r in doc["items"])
        summary = f"Submit {a.doctype} {a.name}: {lines}"
        if a.doctype == "Sales Order":
            summary += f" for {doc['customer']}, total INR {_num(doc.get('grand_total')):,.0f}"
            dues = self.get_customer_dues(CustomerArgs(customer=doc["customer"]))
            if dues["total_overdue_inr"] > 0:
                summary += f" (customer has INR {dues['total_overdue_inr']:,.0f} overdue)"
        else:
            summary += f", needed by {doc.get('schedule_date')}"
        self.approvals_this_request += 1
        if not self.approve(summary):
            return {"document": a.name, "submitted": False, "reason": "the human did NOT approve; it stays a draft"}
        approver = self._approver_client()
        approver.call("frappe.client.submit", doc=approver.get(a.doctype, a.name))
        return {"document": a.name, "submitted": True, "approved_by": os.environ.get("ERP_APPROVER_USER", "approver")}
