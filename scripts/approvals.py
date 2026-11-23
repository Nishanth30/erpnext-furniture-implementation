"""Phase-2 control: purchase orders above ₹1,00,000 need the owner's approval (ERPNext Workflow).

Draft --(Submit for Approval, > ₹1 L)--> Pending Approval --(Approve, owner)--> Approved (submitted)
Draft --(Approve, ≤ ₹1 L)-------------> Approved (small routine buys go straight through)
Pending Approval --(Reject, owner)--> Rejected

Runs after simulate.py: once active, every purchase order must move through these transitions.
"""
import catalog as C
from erp import ERP

erp = ERP()
LIMIT = 100_000
WF = "Purchase Order Approval"


def ensure(doctype, name, doc):
    if not erp.exists(doctype, name):
        erp.insert(dict(doc, doctype=doctype))


def main():
    for state, style in [("Draft", ""), ("Pending Approval", "Warning"), ("Approved", "Success"), ("Rejected", "Danger")]:
        ensure("Workflow State", state, {"workflow_state_name": state, "style": style})
    for action in ["Submit for Approval", "Approve", "Reject"]:
        ensure("Workflow Action Master", action, {"workflow_action_name": action})
    if erp.exists("Workflow", WF):
        print("workflow already active")
        return
    erp.insert({
        "doctype": "Workflow", "workflow_name": WF, "document_type": "Purchase Order", "is_active": 1,
        "workflow_state_field": "workflow_state", "send_email_alert": 0, "override_status": 0,
        "states": [
            {"state": "Draft", "doc_status": "0", "allow_edit": "Purchase User"},
            {"state": "Pending Approval", "doc_status": "0", "allow_edit": "Purchase Manager"},
            {"state": "Approved", "doc_status": "1", "allow_edit": "Purchase Manager"},
            {"state": "Rejected", "doc_status": "0", "allow_edit": "Purchase Manager"},
        ],
        "transitions": [
            {"state": "Draft", "action": "Submit for Approval", "next_state": "Pending Approval",
             "allowed": "Purchase User", "condition": f"doc.grand_total > {LIMIT}", "allow_self_approval": 1},
            {"state": "Draft", "action": "Approve", "next_state": "Approved",
             "allowed": "Purchase User", "condition": f"doc.grand_total <= {LIMIT}", "allow_self_approval": 1},
            {"state": "Pending Approval", "action": "Approve", "next_state": "Approved",
             "allowed": "Purchase Manager", "allow_self_approval": 1},
            {"state": "Pending Approval", "action": "Reject", "next_state": "Rejected",
             "allowed": "Purchase Manager", "allow_self_approval": 1},
        ]})
    print(f"workflow '{WF}' active: POs above ₹{LIMIT:,} need owner approval (Purchase Manager role)")


if __name__ == "__main__":
    main()
