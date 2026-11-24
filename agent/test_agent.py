"""Deterministic tests for the agent: the loop is driven by a scripted fake LLM (no model needed), the tools
run against the live local ERPNext site. Run:  cd agent && ../.venv/bin/python -m pytest -q test_agent.py
"""
import json
import uuid

import pytest

from agent import Agent
from cleanup import cleanup
from erp_api import ERPClient
from tools import ERPTools


class ScriptedLLM:
    """Plays back a fixed list of assistant messages and records what the agent sent each turn."""
    model = "scripted"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, tools=None):
        self.seen.append(messages[:])
        msg = self.replies.pop(0) if self.replies else {"content": "done"}
        return {"message": msg, "latency_s": 0.0, "prompt_tokens": 0, "completion_tokens": 0}


def call(name, args, i=0):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def session():
    s = f"test-{uuid.uuid4().hex[:8]}"
    yield s
    cleanup(s)


def make(session, replies, approve=None):
    llm = ScriptedLLM(replies)
    return Agent(llm, ERPTools(ERPClient(), session=session, approve=approve), trace=False), llm


def test_tool_result_reaches_model_and_answer_is_returned(session):
    agent, llm = make(session, [{"content": "", "tool_calls": [call("get_stock", {"item_code": "HW-HINGE-SC"})]},
                                {"content": "We have 141 hinges."}])
    res = agent.run("how many soft close hinges?")
    assert res.answer == "We have 141 hinges." and res.steps == 2
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["total_on_hand"] == 141


def test_bad_arguments_are_returned_as_errors_and_counted(session):
    agent, llm = make(session, [{"content": "", "tool_calls": [call("check_build_capacity", {"item_code": "WRD-3D", "qty": "ten"})]},
                                {"content": "", "tool_calls": [call("check_build_capacity", {"item_code": "WRD-3D", "qty": 10}, 1)]},
                                {"content": "Not enough."}])
    res = agent.run("can we build ten 3-door wardrobes?")
    assert [c["status"] for c in res.tool_calls] == ["invalid_args", "ok"]
    assert res.invalid_calls == 1
    assert "invalid arguments" in llm.seen[1][-1]["content"]


def test_unknown_tool_and_repeated_calls_are_refused(session):
    same = call("search_customers", {"query": "kovai"})
    agent, _ = make(session, [{"content": "", "tool_calls": [call("delete_invoice", {"name": "x"})]},
                              {"content": "", "tool_calls": [same]}, {"content": "", "tool_calls": [same]},
                              {"content": "", "tool_calls": [same]}, {"content": "ok"}])
    res = agent.run("x")
    assert [c["status"] for c in res.tool_calls] == ["unknown_tool", "ok", "ok", "repeated"]


def test_step_budget_stops_a_looping_model(session):
    agent, _ = make(session, [{"content": "", "tool_calls": [call("search_items", {"query": f"wardrobe {i}"}, i)]}
                              for i in range(20)])
    agent.max_steps = 4
    res = agent.run("loop forever")
    assert res.stopped and res.steps == 4 and "nothing was submitted" in res.answer


def test_tool_call_printed_as_text_is_recovered(session):
    text = '<tool_call>\n{"name": "get_stock", "arguments": {"item_code": "HW-LOCK"}}\n</tool_call>'
    agent, _ = make(session, [{"content": text}, {"content": "10 locks."}])
    res = agent.run("locks?")
    assert res.recovered_text_calls == 1 and res.tool_calls[0]["status"] == "ok"


def test_draft_order_is_idempotent_and_flags_overdue_customer(session):
    t = ERPTools(ERPClient(), session=session)
    args = {"customer": "Kovai Home Centre", "lines": [{"item_code": "MND-3X2", "qty": 1}]}
    first, s1 = t.execute("create_draft_sales_order", args)
    again, s2 = t.execute("create_draft_sales_order", args)
    assert s1 == s2 == "ok"
    assert first["draft_sales_order"] == again["draft_sales_order"] and again["already_existed"]
    assert "overdue" in first["credit_warning"]
    so = ERPClient().get("Sales Order", first["draft_sales_order"])
    assert so["docstatus"] == 0 and so["custom_agent_ref"].startswith(session)


def test_submit_needs_human_approval_and_own_draft(session):
    approvals = []
    t = ERPTools(ERPClient(), session=session, approve=lambda s: approvals.append(s) or False)
    so = t.execute("create_draft_sales_order", {"customer": "Kanchi Furniture Mart",
                                                "lines": [{"item_code": "OFT-5FT", "qty": 1}]})[0]["draft_sales_order"]
    denied, _ = t.execute("submit_document", {"doctype": "Sales Order", "name": so})
    assert denied["submitted"] is False and len(approvals) == 1
    assert ERPClient().get("Sales Order", so)["docstatus"] == 0
    foreign, status = t.execute("submit_document", {"doctype": "Sales Order", "name": "SAL-ORD-2026-00073"})
    assert status == "refused" and "not drafted by this agent" in foreign["error"]


def test_draft_with_submit_flag_goes_through_the_human_gate(session):
    approvals = []
    t = ERPTools(ERPClient(), session=session, approve=lambda s: approvals.append(s) or False)
    r, s = t.execute("create_draft_sales_order", {"customer": "Kovai Home Centre", "submit": True,
                                                  "lines": [{"item": "2 office table 5 ft", "qty": 2}]})
    assert s == "ok" and r["submission"]["submitted"] is False
    assert len(approvals) == 1 and "overdue" in approvals[0]        # the approver sees the credit risk
    assert ERPClient().get("Sales Order", r["draft_sales_order"])["docstatus"] == 0


def test_answer_always_names_created_drafts(session):
    agent, _ = make(session, [{"content": "", "tool_calls": [call("create_draft_material_request",
                                                                  {"lines": [{"item": "wardrobe locks", "qty": 5}]})]},
                              {"content": "Done."}])
    res = agent.run("buy 5 wardrobe locks")
    name = next(iter(res.created))
    assert res.model_answer == "Done." and name in res.answer and "Draft" in res.answer


def test_business_rules_reject_wrong_documents(session):
    t = ERPTools(ERPClient(), session=session)
    _, s1 = t.execute("create_draft_material_request", {"lines": [{"item_code": "WRD-3D", "qty": 2}], "reason": "for stock"})
    _, s2 = t.execute("create_draft_sales_order", {"customer": "Kanchi Furniture Mart", "lines": [{"item_code": "HW-LOCK", "qty": 2}]})
    r3, s3 = t.execute("create_draft_sales_order", {"customer": "Home", "lines": [{"item_code": "OFT-5FT", "qty": 1}]})
    _, s4 = t.execute("create_draft_sales_order", {"customer": "Kanchi Furniture Mart", "lines": [{"item_code": "OFT-5FT", "qty": 1}],
                                                   "delivery_date": "2020-01-01"})
    r5, s5 = t.execute("create_draft_sales_order", {"customer": "Kanchi", "lines": [{"item_code": "wardrobe", "qty": 1}]})
    assert (s1, s2, s3, s4, s5) == ("refused",) * 5
    assert "Kovai Home Centre" in r3["error"] and "Mega Home Stores - Padi" in r3["error"]   # ambiguous: lists both
    assert "WRD-3D" in r5["error"]                                                          # ambiguous item: lists options


def test_unique_partial_names_resolve(session):
    t = ERPTools(ERPClient(), session=session)
    r, s = t.execute("create_draft_sales_order", {"customer": "Mega Home Padi", "lines": [{"item_code": "office table 5 ft", "qty": 1}]})
    assert s == "ok" and r["customer"] == "Mega Home Stores - Padi" and r["lines"][0]["item_code"] == "OFT-5FT"


def test_arguments_the_user_never_gave_are_refused(session):
    """Grounding: '2 wardrobes' must not become '2-door', 'the Home store' must not become one customer,
    and a code must come from the user or a tool result, never from the model's imagination."""
    agent, _ = make(session, [
        {"content": "", "tool_calls": [call("create_draft_sales_order", {"customer": "Mega Home Stores",
                                                                          "lines": [{"item": "2-door wardrobe", "qty": 2}]})]},
        {"content": "", "tool_calls": [call("create_draft_sales_order", {"customer": "Kovai Home Centre",
                                                                          "lines": [{"item": "WRD-3D", "qty": 2}]}, 1)]},
        {"content": "Which wardrobe: 2, 3 or 4 doors?"}])
    res = agent.run("Draft an order for 2 wardrobes for Mega Home Stores.")
    assert [c["status"] for c in res.tool_calls] == ["refused", "refused"] and not res.created
    agent, _ = make(session, [
        {"content": "", "tool_calls": [call("create_draft_sales_order", {"customer": "Mega Home Stores - Padi",
                                                                          "lines": [{"item": "office table 5 ft", "qty": 1}]})]},
        {"content": "Which Home store?"}])
    res = agent.run("Draft an order for the Home store: 1 office table 5 ft.")
    assert res.tool_calls[0]["status"] == "refused" and not res.created


def test_codes_seen_in_tool_results_are_allowed(session):
    agent, _ = make(session, [
        {"content": "", "tool_calls": [call("search_items", {"query": "office table"})]},
        {"content": "", "tool_calls": [call("create_draft_sales_order", {"customer": "Kanchi Furniture Mart",
                                                                          "lines": [{"item": "OFT-5FT", "qty": 1}]}, 1)]},
        {"content": "Drafted."}])
    res = agent.run("Kanchi needs one office table")
    assert res.tool_calls[1]["status"] == "ok" and res.created


def test_submit_only_when_the_user_asked(session):
    approvals = []
    agent, _ = make(session, [{"content": "", "tool_calls": [call("create_draft_sales_order", {
        "customer": "Kanchi Furniture Mart", "submit": True, "lines": [{"item": "office table 5 ft", "qty": 1}]})]},
                              {"content": "ok"}], approve=lambda s: approvals.append(s) or True)
    res = agent.run("Draft an order for Kanchi: 1 office table 5 ft")           # no 'submit' in the request
    assert res.tool_calls[0]["status"] == "ok" and not approvals                # draft made, submit ignored
    assert all(status.startswith("Draft") for status in res.created.values())


def test_approved_submit_runs_as_the_owner(session):
    agent, _ = make(session, [{"content": "", "tool_calls": [call("create_draft_material_request", {
        "submit": True, "lines": [{"item": "wardrobe locks", "qty": 4}]})]}, {"content": "Submitted."}],
                    approve=lambda s: True)
    res = agent.run("Raise a purchase request for 4 wardrobe locks and submit it")
    name = next(iter(res.created))
    assert res.created[name] == "submitted"
    doc = ERPClient().get("Material Request", name)
    assert doc["docstatus"] == 1 and doc["modified_by"] == "owner@kfw.test"


def test_erpnext_itself_blocks_destructive_calls_for_the_agent_user(session):
    from erp_api import ERPError
    e = ERPClient()
    t = ERPTools(e, session=session)
    so = t.execute("create_draft_sales_order", {"customer": "Kanchi Furniture Mart", "lines": [{"item": "OFT-5FT", "qty": 1}]})[0]
    for attempt in (lambda: e.call("frappe.client.submit", doc=e.get("Sales Order", so["draft_sales_order"])),
                    lambda: e._req("DELETE", f"/api/resource/Sales Order/{so['draft_sales_order']}"),
                    lambda: e.call("frappe.client.cancel", doctype="Sales Order", name="SAL-ORD-2026-00074"),
                    lambda: e.insert({"doctype": "Payment Entry", "payment_type": "Receive", "party_type": "Customer",
                                      "party": "Kovai Home Centre", "paid_amount": 1, "received_amount": 1})):
        with pytest.raises(ERPError):
            attempt()


def test_grounding_uses_the_current_message_in_a_conversation(session):
    """Demo finding: '2 wardrobes' passed because an EARLIER turn had said 'Wardrobe 3-Door'."""
    agent, _ = make(session, [{"content": "Short of edge band."},
                              {"content": "", "tool_calls": [call("create_draft_sales_order", {
                                  "customer": "Mega Home Stores", "lines": [{"item": "Wardrobe 3-Door", "qty": 2}]})]},
                              {"content": "Which wardrobe?"}])
    first = agent.run("Can we make 10 Wardrobe 3-Door with the material we have?")
    res = agent.run("Draft an order for 2 wardrobes for Mega Home Stores.", first.messages)
    assert res.tool_calls[0]["status"] == "refused" and not res.created


def test_unrequested_submit_flag_still_creates_the_draft(session):
    """Demo finding: submit=true without being asked used to fail the whole call, so no draft was made."""
    approvals = []
    agent, _ = make(session, [{"content": "", "tool_calls": [call("create_draft_material_request", {
        "submit": True, "lines": [{"item": "wardrobe locks", "qty": 5}]})]}, {"content": "Drafted."}],
                    approve=lambda s: approvals.append(s) or True)
    res = agent.run("Raise a purchase request for 5 wardrobe locks")
    name = next(iter(res.created))
    assert res.tool_calls[0]["status"] == "ok" and not approvals
    assert ERPClient().get("Material Request", name)["docstatus"] == 0
