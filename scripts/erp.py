"""Minimal ERPNext REST client (Frappe /api/resource and /api/method).

Local prototype only: connects to the Docker site at http://localhost:8080 with the
quick-start Administrator login. Credentials come from the environment when set.
"""
import json
import os
import subprocess

import requests

BASE = os.environ.get("ERP_URL", "http://localhost:8080")
USER = os.environ.get("ERP_USER", "Administrator")
PASSWORD = os.environ.get("ERP_PASSWORD", "admin")


class ERPError(Exception):
    pass


class ERP:
    def __init__(self):
        self.s = requests.Session()
        r = self.s.post(f"{BASE}/api/method/login", data={"usr": USER, "pwd": PASSWORD}, timeout=60)
        if r.status_code != 200:
            raise ERPError(f"login failed: {r.status_code} {r.text[:200]}")

    # ------------------------------------------------------------ low level
    def _check(self, r):
        if r.status_code >= 400:
            msg = r.text
            try:
                body = r.json()
                if "_server_messages" in body:
                    msgs = json.loads(body["_server_messages"])
                    msg = " | ".join(json.loads(m).get("message", m) for m in msgs)
                elif "exception" in body:
                    msg = body["exception"]
            except Exception:
                pass
            raise ERPError(f"{r.status_code}: {msg[:600]}")
        return r.json()

    def get(self, doctype, name):
        return self._check(self.s.get(f"{BASE}/api/resource/{doctype}/{requests.utils.quote(name, safe='')}"))["data"]

    def exists(self, doctype, name):
        r = self.s.get(f"{BASE}/api/resource/{doctype}/{requests.utils.quote(name, safe='')}")
        return r.status_code == 200

    def list(self, doctype, filters=None, fields=None, limit=0, order_by=None):
        params = {"limit_page_length": limit or 100000}
        if filters is not None:
            params["filters"] = json.dumps(filters)
        if fields:
            params["fields"] = json.dumps(fields)
        if order_by:
            params["order_by"] = order_by
        return self._check(self.s.get(f"{BASE}/api/resource/{doctype}", params=params))["data"]

    def insert(self, doc):
        doctype = doc["doctype"]
        return self._check(self.s.post(f"{BASE}/api/resource/{doctype}", json=doc))["data"]

    def update(self, doctype, name, values):
        return self._check(self.s.put(f"{BASE}/api/resource/{doctype}/{requests.utils.quote(name, safe='')}",
                                      json=values))["data"]

    def submit(self, doc):
        """Insert (if needed) and submit a submittable document."""
        if not doc.get("name") or not self.exists(doc["doctype"], doc["name"]):
            doc = self.insert(doc)
        return self.call("frappe.client.submit", doc=json.dumps(doc))

    def call(self, method, **kwargs):
        r = self.s.post(f"{BASE}/api/method/{method}", json=kwargs)
        return self._check(r).get("message")

    def get_value(self, doctype, filters, fieldname):
        return self.call("frappe.client.get_value", doctype=doctype, filters=filters, fieldname=fieldname)

    # ------------------------------------------------------------ helpers
    def ensure(self, doc, key=None):
        """Idempotent insert: skip if a document with this name (or key filter) already exists."""
        doctype = doc["doctype"]
        name = doc.get("name") or doc.get(key or "name")
        if key and not doc.get("name"):
            found = self.list(doctype, filters={key: doc[key]}, fields=["name"], limit=1)
            if found:
                return found[0]
        elif name and self.exists(doctype, name):
            return self.get(doctype, name)
        return self.insert(doc)


def sql(query):
    """Run read-only SQL against the site DB through bench (used by reconciliation/reports)."""
    cmd = ["docker", "exec", "-i", "kfw-erp-backend-1", "bench", "--site", "frontend", "mariadb", "-B", "-e", query]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    return [dict(zip(header, ln.split("\t"))) for ln in lines[1:]]
