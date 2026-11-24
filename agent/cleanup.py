"""Delete drafts the agent created in test/eval sessions (runs as Administrator: the agent itself cannot delete).

    ../.venv/bin/python cleanup.py eval-      # every draft whose custom_agent_ref starts with 'eval-'
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from erp import BASE, ERP  # noqa: E402


def cleanup(prefix: str) -> int:
    if not prefix.startswith(("eval-", "test-", "probe")) or ":" in prefix:
        raise ValueError("only evaluation/test sessions (eval-*, test-*, probe*) can be cleaned up")
    erp = ERP()
    n = 0
    for dt in ("Sales Order", "Material Request"):
        for d in erp.list(dt, filters=[["custom_agent_ref", "like", f"{prefix}%"]], fields=["name", "docstatus"]):
            if d["docstatus"] == 1:
                erp.call("frappe.client.cancel", doctype=dt, name=d["name"])
            r = erp.s.delete(f"{BASE}/api/resource/{dt}/{requests.utils.quote(d['name'], safe='')}")
            if r.status_code >= 400:
                raise RuntimeError(f"could not delete {dt} {d['name']}: {r.text[:200]}")
            n += 1
    return n


if __name__ == "__main__":
    print(f"deleted {cleanup(sys.argv[1])} agent drafts")
