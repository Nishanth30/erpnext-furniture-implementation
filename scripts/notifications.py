"""In-app alerts (ERPNext Notification, channel 'System Notification' = the bell icon). Idempotent.

  * New confirmed order        -> owner + accountant + store (so production can plan)
  * PO waiting for approval    -> owner
Email/WhatsApp can be added to the same Notification once an email account or a WhatsApp provider is configured.
"""
from erp import ERP

erp = ERP()

NOTIFICATIONS = {
    "New Order Confirmed": {
        "document_type": "Sales Order", "event": "Submit", "channel": "System Notification",
        "subject": "New order {{ doc.name }}: {{ doc.customer }}, {{ doc.get_formatted('grand_total') }}",
        "message": "{% if doc.custom_intake_ref %}<p>Sent from the retailer floor by {{ doc.custom_retailer_staff }}.</p>{% endif %}"
                   "{% if doc.custom_order_kind == 'Custom' %}<p><b>Custom order</b>, signed specification attached.</p>{% endif %}"
                   "<p><b>{{ doc.customer }}</b> ordered:</p><ul>{% for i in doc.items %}"
                   "<li>{{ i.qty|int }} × {{ i.item_name }}</li>{% endfor %}</ul>"
                   "<p>Deliver by {{ frappe.utils.formatdate(doc.delivery_date) }}.</p>",
        "recipients": [{"receiver_by_role": r} for r in ("Sales Manager", "Accounts User", "Stock User")],
    },
    "Purchase Order Needs Approval": {
        "document_type": "Purchase Order", "event": "Value Change", "value_changed": "workflow_state",
        "condition": "doc.workflow_state == 'Pending Approval'", "channel": "System Notification",
        "subject": "Approve PO {{ doc.name }}: {{ doc.supplier }}, {{ doc.get_formatted('grand_total') }}",
        "message": "<p>{{ doc.supplier }}: {{ doc.items|length }} line(s), total {{ doc.get_formatted('grand_total') }}. "
                   "Open the order and choose Approve or Reject.</p>",
        "recipients": [{"receiver_by_role": "Purchase Manager"}],
    },
}


def main():
    for name, cfg in NOTIFICATIONS.items():
        doc = dict(cfg, enabled=1)
        if erp.exists("Notification", name):
            erp.update("Notification", name, doc)
        else:
            erp.insert(dict(doc, doctype="Notification", name=name))
        print("notification ready:", name)


if __name__ == "__main__":
    main()
