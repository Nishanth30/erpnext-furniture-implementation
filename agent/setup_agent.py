"""One-time ERPNext setup for the operations agent (idempotent; re-run safely).

Least privilege, enforced by ERPNext itself rather than only by the Python tool layer:
  * agent@kfw.test holds exactly two custom roles:
      - "Agent Reader":  read-only on the masters and documents its read tools use
      - "Agent Drafter": create/write on Sales Order and Material Request; no submit, cancel, amend or delete
    so even a leaked API key or a buggy tool cannot confirm, cancel or delete anything.
  * Submitting a draft is done with the APPROVER's own API key (the owner, owner@kfw.test), only after that
    human says yes. ERPNext then records the owner, not the agent, as the person who confirmed it.
  * `custom_agent_ref` on Sales Order and Material Request: session + idempotency key of the agent request.
Keys go to agent/.env (git-ignored).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from erp import BASE, ERP  # noqa: E402

AGENT = "agent@kfw.test"
APPROVER = "owner@kfw.test"
READER, DRAFTER = "Agent Reader", "Agent Drafter"
READ_DOCTYPES = ["Customer", "Item", "Item Price", "Bin", "BOM", "Sales Invoice", "Sales Order", "Material Request",
                 "Warehouse", "Item Group", "Sales Taxes and Charges Template", "Payment Terms Template"]
DRAFT_DOCTYPES = ["Sales Order", "Material Request"]
PM = "frappe.core.page.permission_manager.permission_manager"

erp = ERP()

for dt, after in (("Sales Order", "custom_order_kind"), ("Material Request", "material_request_type")):
    if not erp.list("Custom Field", filters={"dt": dt, "fieldname": "custom_agent_ref"}, fields=["name"]):
        erp.insert({"doctype": "Custom Field", "dt": dt, "fieldname": "custom_agent_ref", "label": "Agent Ref",
                    "fieldtype": "Data", "unique": 1, "read_only": 1, "no_copy": 1, "insert_after": after,
                    "description": "Session and idempotency key of the operations agent that drafted this document"})


def grant(role, doctype, ptypes):
    if not erp.list("Custom DocPerm", filters={"parent": doctype, "role": role, "permlevel": 0}, fields=["name"]):
        erp.call(f"{PM}.add", parent=doctype, role=role, permlevel=0)
    for p in ("read", "select", "write", "create", "submit", "cancel", "amend", "delete", "report", "export", "print", "email", "share"):
        erp.call(f"{PM}.update", doctype=doctype, role=role, permlevel=0, ptype=p, value=1 if p in ptypes else 0)


for role in (READER, DRAFTER):
    if not erp.exists("Role", role):
        erp.insert({"doctype": "Role", "role_name": role, "desk_access": 0})
for dt in READ_DOCTYPES:
    grant(READER, dt, {"read", "select"})
for dt in DRAFT_DOCTYPES:
    grant(DRAFTER, dt, {"read", "select", "write", "create"})

if not erp.exists("User", AGENT):
    erp.insert({"doctype": "User", "email": AGENT, "first_name": "Operations Agent", "last_name": "Integration",
                "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": READER}, {"role": DRAFTER}]})
else:                                   # replace whatever roles it had (earlier versions used broad stock roles)
    erp.update("User", AGENT, {"roles": [{"role": READER}, {"role": DRAFTER}], "role_profile_name": None})

agent_secret = erp.call("frappe.core.doctype.user.user.generate_keys", user=AGENT)["api_secret"]
approver_secret = erp.call("frappe.core.doctype.user.user.generate_keys", user=APPROVER)["api_secret"]
env = HERE / ".env"
keep = [ln for ln in (env.read_text().splitlines() if env.exists() else [])
        if ln and not ln.startswith(("ERP_URL=", "ERP_API_KEY=", "ERP_API_SECRET=", "ERP_APPROVER_"))]
env.write_text("\n".join([f"ERP_URL={BASE}",
                          f"ERP_API_KEY={erp.get('User', AGENT)['api_key']}", f"ERP_API_SECRET={agent_secret}",
                          f"ERP_APPROVER_USER={APPROVER}",
                          f"ERP_APPROVER_KEY={erp.get('User', APPROVER)['api_key']}", f"ERP_APPROVER_SECRET={approver_secret}",
                          *keep]) + "\n")
print(f"{AGENT}: roles {READER}, {DRAFTER}; submits run as {APPROVER} after human approval. Keys in {env} (git-ignored)")
