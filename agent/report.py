"""Summarise evaluation results into markdown tables for docs/11-ai-operations-agent.md.

    cd agent && ../.venv/bin/python report.py v1-baseline v2
Reads out/agent_eval/<version>/<model>[-heldout].json for each version given.
"""
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from eval_tasks import SETS

OUT = Path(__file__).resolve().parent.parent / "out" / "agent_eval"
CATS = ["lookup", "reasoning", "action", "safety"]


def load(versions):
    runs = {}                                   # (version, model, set) -> data
    for v in versions:
        for p in sorted((OUT / v).glob("*.json")):
            if p.stem.endswith("-subset"):
                continue
            d = json.loads(p.read_text())
            runs[(v, d["model"], d.get("set", "dev"))] = d
    return runs


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def summary(d):
    rs = d["results"]
    by_task = defaultdict(list)
    for r in rs:
        by_task[r["task"]].append(r["passed"])
    calls = [c for r in rs for c in r.get("tool_calls", [])]
    lat = sorted(r["latency_s"] for r in rs if "latency_s" in r)
    return {
        "trials": len(rs), "passed": sum(r["passed"] for r in rs),
        "all_trials": sum(all(v) for v in by_task.values()), "tasks": len(by_task),
        "cat": {c: (sum(r["passed"] for r in rs if r["category"] == c), sum(1 for r in rs if r["category"] == c)) for c in CATS},
        "median_latency": statistics.median(lat) if lat else 0, "p90_latency": lat[int(0.9 * (len(lat) - 1))] if lat else 0,
        "calls_per_task": len(calls) / len(rs) if rs else 0,
        "invalid": sum(r.get("invalid_calls", 0) for r in rs), "n_calls": len(calls),
        "failed_calls": sum(1 for c in calls if c["status"] != "ok"),
        "corrected": sum(1 for r in rs if any(c["status"] != "ok" for c in r.get("tool_calls", [])) and r["passed"]),
        "stopped": sum(r.get("stopped", False) for r in rs),
        "recovered": sum(r.get("recovered_text_calls", 0) for r in rs),
        "unasked_submits": sum(1 for r in rs if r["task"] != "submit_denied"
                               and (r.get("approval_requests") or any(c["name"] == "submit_document" for c in r.get("tool_calls", [])))),
        "by_task": by_task,
    }


ROWS = [
    ("Trials passed", lambda s: f"**{s['passed']}/{s['trials']} ({pct(s['passed'], s['trials'])})**"),
    ("Tasks passed in all 3 trials", lambda s: f"{s['all_trials']}/{s['tasks']}"),
    *[(f"{c.title()}", (lambda c: lambda s: f"{s['cat'][c][0]}/{s['cat'][c][1]}" if s['cat'][c][1] else "-")(c)) for c in CATS],
    ("Tool calls per task (mean)", lambda s: f"{s['calls_per_task']:.1f}"),
    ("Tool calls that errored", lambda s: f"{s['failed_calls']}/{s['n_calls']}"),
    ("Runs with an errored call that still passed", lambda s: f"{s['corrected']}"),
    ("Runs stopped by a budget", lambda s: f"{s['stopped']}"),
    ("Submits nobody asked for", lambda s: f"{s['unasked_submits']}"),
    ("Latency per task, median / p90", lambda s: f"{s['median_latency']:.0f} s / {s['p90_latency']:.0f} s"),
]


def table(cols, S):
    print("| | " + " | ".join(cols) + " |")
    print("|---|" + "---|" * len(cols))
    for name, f in ROWS:
        print(f"| {name} | " + " | ".join(f(S[k]) for k in S) + " |")


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def headline(runs, version):
    """Task-level pass rates (a task passes if it passed in every trial) with 95% Wilson intervals."""
    print(f"### Headline ({version})\n")
    print("| Model | Set | Tasks passed | 95% CI | Lookup | Reasoning | Action | Safety |")
    print("|---|---|---|---|---|---|---|---|")
    pooled = defaultdict(lambda: [0, 0])
    for (v, m, s), d in sorted(runs.items(), key=lambda kv: (kv[0][1], list(SETS).index(kv[0][2]))):
        if v != version:
            continue
        by = defaultdict(list)
        cat = {}
        for r in d["results"]:
            by[r["task"]].append(r["passed"])
            cat[r["task"]] = r["category"]
        k, n = sum(all(x) for x in by.values()), len(by)
        lo, hi = wilson(k, n)
        cats = {c: (sum(all(by[t]) for t in by if cat[t] == c), sum(1 for t in by if cat[t] == c)) for c in CATS}
        print(f"| `{m}` | {s} | **{k}/{n}** | {100 * lo:.0f}–{100 * hi:.0f}% | " +
              " | ".join(f"{a}/{b}" if b else "-" for a, b in cats.values()) + " |")
        if s in ("heldout_c", "heldout_d"):
            pooled[m][0] += k
            pooled[m][1] += n
    for m, (k, n) in pooled.items():
        lo, hi = wilson(k, n)
        print(f"| `{m}` | **unseen C+D** | **{k}/{n} ({100 * k / n:.0f}%)** | {100 * lo:.0f}–{100 * hi:.0f}% | | | | |")
    print()


def main():
    versions = sys.argv[1:] or ["v1-baseline", "v2"]
    runs = load(versions)
    headline(runs, versions[-1])
    S = {k: summary(d) for k, d in runs.items()}
    order = sorted(S, key=lambda k: (k[1], list(SETS).index(k[2]), versions.index(k[0])))
    print("### Summary\n")
    table([f"`{m}` {s} {v}" for v, m, s in order], {k: S[k] for k in order})
    for set_name, tasks in SETS.items():
        keys = [k for k in order if k[2] == set_name]
        if not keys:
            continue
        print(f"\n### Per task ({set_name})\n")
        print("| Task | Category | " + " | ".join(f"`{m}` {v}" for v, m, _ in keys) + " |")
        print("|---|---|" + "---|" * len(keys))
        for t in tasks:
            cells = [f"{sum(S[k]['by_task'].get(t.id, []))}/{len(S[k]['by_task'].get(t.id, []))}" for k in keys]
            print(f"| `{t.id}` | {t.category} | " + " | ".join(cells) + " |")
    print("\n### Failures in the latest version\n")
    latest = versions[-1]
    for (v, m, s), d in runs.items():
        if v != latest:
            continue
        for r in d["results"]:
            if not r["passed"]:
                print(f"- `{m}` {s} t{r['trial']} `{r['task']}`: {r['why']} | calls: "
                      f"{[c['name'] + ('!' if c['status'] != 'ok' else '') for c in r.get('tool_calls', [])]} | "
                      f"answer: {r.get('answer', '')[:170]!r}")


if __name__ == "__main__":
    main()
