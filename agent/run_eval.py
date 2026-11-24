"""Run the evaluation suite against one model and save per-trial results.

    cd agent && ../.venv/bin/python run_eval.py --model qwen2.5:7b --trials 3
    ../.venv/bin/python run_eval.py --model qwen2.5:7b --only draft_so,ambiguous     # a subset

Each trial runs in its own session; every draft it created is deleted afterwards, so trials and tasks
cannot see each other's documents. Results: ../out/agent_eval/<model>.json
"""
import argparse
import json
import time
import uuid
from pathlib import Path

from agent import Agent
from cleanup import cleanup
from erp_api import ERPClient
from eval_tasks import SETS, Ctx, session_docs
from llm import ChatLLM, LLMError
from tools import ERPTools

OUT = Path(__file__).resolve().parent.parent / "out" / "agent_eval"


def run_task(task, model, temperature, seed, erp):
    session = f"eval-{uuid.uuid4().hex[:8]}"
    asked = []                                       # every time the agent asked the simulated human to approve
    tools = ERPTools(erp, session=session, approve=lambda summary: asked.append(summary) or task.approve)
    agent = Agent(ChatLLM(model=model, temperature=temperature, seed=seed), tools)
    try:
        try:
            res = agent.run(task.prompt)
        except Exception as e:                       # LLM outage or a bug: a failed trial, not a crashed run
            return {"task": task.id, "category": task.category, "passed": False, "session": session,
                    "why": f"{'LLM error' if isinstance(e, LLMError) else 'run error'}: {type(e).__name__}: {str(e)[:200]}"}
        sos, mrs = session_docs(erp, session)
        ctx = Ctx(answer=res.model_answer, run=res, sos=sos, mrs=mrs, approvals=len(asked))
        try:
            passed, why = task.check(ctx)
        except Exception as e:                      # a grader crash is a failed trial, not a crashed run
            passed, why = False, f"grader error: {type(e).__name__}: {e}"
        return {"task": task.id, "category": task.category, "passed": passed, "why": why, "session": session,
                "answer": res.model_answer, "shown_to_user": res.answer, "steps": res.steps, "llm_calls": res.llm_calls, "stopped": res.stopped,
                "tool_calls": [{k: c[k] for k in ("name", "status", "ms")} | {"args": c["args"]} for c in res.tool_calls],
                "invalid_calls": res.invalid_calls, "recovered_text_calls": res.recovered_text_calls,
                "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens, "latency_s": res.latency_s,
                "docs": [d["name"] for d in sos + mrs], "approval_requests": len(asked)}
    finally:
        cleanup(session)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--temperature", type=float, default=0.0, help="0 = what the CLI uses")
    ap.add_argument("--only", default="")
    ap.add_argument("--set", choices=list(SETS), default="dev")
    ap.add_argument("--tag", default="", help="results subfolder, e.g. v2")
    args = ap.parse_args()
    pool = SETS[args.set]
    tasks = [t for t in pool if not args.only or t.id in args.only.split(",")]
    erp = ERPClient()
    out = OUT / args.tag if args.tag else OUT
    out.mkdir(parents=True, exist_ok=True)
    stem = args.model.replace(":", "_").replace("/", "_")
    path = out / f"{stem}{'-' + args.set if args.set != 'dev' else ''}{'-subset' if args.only else ''}.json"
    results, t0 = [], time.time()
    for trial in range(args.trials):
        for t in tasks:
            r = run_task(t, args.model, args.temperature, seed=trial + 1, erp=erp)
            r["trial"] = trial + 1
            results.append(r)
            print(f"[{args.model} t{trial + 1}] {t.id:16s} {'PASS' if r['passed'] else 'FAIL'} "
                  f"{r.get('latency_s', 0):6.1f}s {r.get('why', '')[:110]}", flush=True)
            path.write_text(json.dumps({"model": args.model, "set": args.set, "temperature": args.temperature, "trials": args.trials,
                                        "results": results}, indent=1, default=str))
    n = len(results)
    print(f"\n{args.model}: {sum(r['passed'] for r in results)}/{n} trials passed in {time.time() - t0:.0f}s -> {path}")


if __name__ == "__main__":
    main()
