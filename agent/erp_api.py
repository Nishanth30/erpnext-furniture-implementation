"""Token-authenticated ERPNext REST client used by the agent's tools (integration user agent@kfw.test)."""
import json
import os
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


def load_env(path=HERE / ".env"):
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()


class ERPError(Exception):
    """ERPNext refused the request; the message is safe to show to the model."""


class ERPClient:
    def __init__(self, url=None, key=None, secret=None, timeout=20):
        self.url = (url or os.environ.get("ERP_URL", "http://localhost:8080")).rstrip("/")
        key = key or os.environ["ERP_API_KEY"]
        secret = secret or os.environ["ERP_API_SECRET"]
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"token {key}:{secret}", "Accept": "application/json"})
        self.timeout = timeout

    def _req(self, method, path, **kw):
        r = self.s.request(method, f"{self.url}{path}", timeout=self.timeout, **kw)
        if r.status_code >= 400:
            msg = r.text
            try:
                body = r.json()
                if "_server_messages" in body:
                    msg = " | ".join(json.loads(m).get("message", m) for m in json.loads(body["_server_messages"]))
                elif "exception" in body:
                    msg = body["exception"]
            except ValueError:
                pass
            raise ERPError(f"ERPNext {r.status_code}: {str(msg)[:300]}")
        return r.json()

    def list(self, doctype, filters=None, fields=None, order_by=None, limit=500):
        params = {"limit_page_length": limit}
        if filters is not None:
            params["filters"] = json.dumps(filters)
        if fields:
            params["fields"] = json.dumps(fields)
        if order_by:
            params["order_by"] = order_by
        return self._req("GET", f"/api/resource/{doctype}", params=params)["data"]

    def get(self, doctype, name):
        return self._req("GET", f"/api/resource/{doctype}/{requests.utils.quote(name, safe='')}")["data"]

    def insert(self, doc):
        return self._req("POST", f"/api/resource/{doc['doctype']}", json=doc)["data"]

    def call(self, method, **kwargs):
        return self._req("POST", f"/api/method/{method}", json=kwargs).get("message")
