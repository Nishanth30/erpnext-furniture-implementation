"""One-time ERPNext setup for the intake service: idempotency field, integration user, API key (.env)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
from erp import BASE, ERP  # noqa: E402

erp = ERP()
if not erp.list("Custom Field", filters={"dt": "Sales Order", "fieldname": "custom_intake_ref"}, fields=["name"]):
    erp.insert({"doctype": "Custom Field", "dt": "Sales Order", "fieldname": "custom_intake_ref", "label": "Intake Ref",
                "fieldtype": "Data", "unique": 1, "read_only": 1, "no_copy": 1, "insert_after": "custom_order_kind",
                "description": "Idempotency key from the retail order intake service"})
USER = "intake@kfw.test"
if not erp.exists("User", USER):
    erp.insert({"doctype": "User", "email": USER, "first_name": "Order Intake", "last_name": "Integration",
                "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": "Sales User"}]})
secret = erp.call("frappe.core.doctype.user.user.generate_keys", user=USER)["api_secret"]
key = erp.get("User", USER)["api_key"]
(HERE / ".env").write_text(f"ERP_URL={BASE}\nERP_API_KEY={key}\nERP_API_SECRET={secret}\n")
print("integration user ready; API key written to .env (git-ignored)")
