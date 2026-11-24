"""The operations agent: an LLM tool-calling loop over the ERPNext tools.

Loop: send the conversation and tool schemas to the model -> if it asks for tools, validate and run them
and append the results -> repeat until it answers in plain text or hits a budget.

Guardrails in the loop (the tools add grounding, business rules and the approval gate):
  * budgets per user message: 10 steps, 6 failed tool calls, the same call at most twice;
  * malformed tool calls (bad JSON, unknown tool, wrong argument types) come back to the model as errors
    it can fix, and are counted so the evaluation can measure self-correction;
  * a message that is nothing but a tool call printed as text (a small-model habit) is executed; a call
    mentioned inside an ordinary sentence is not;
  * the user always sees every document that was created and its real status, whatever the model wrote;
  * every step is written to a JSONL trace (agent/traces/) for debugging and monitoring.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from llm import ChatLLM
from tools import SUBMIT_WORDS, ERPTools

MAX_STEPS = 10
MAX_SAME_CALL = 2
MAX_FAILED_CALLS = 6
TRACE_DIR = Path(__file__).resolve().parent / "traces"

SYSTEM_PROMPT = """You are the operations assistant of Kaveri Furniture Works, a furniture factory in Chennai that \
sells wardrobes, cots, pooja mandirs and office tables to retailers. Today is {today}. Amounts are in INR.

Rules:
1. Get every fact (stock, prices, dues, invoice counts, orders, codes) from a tool result. Never invent or estimate
   a number. If the result you have does not contain the number asked for, call the tool that does.
2. Before using a product or customer in any other tool, look it up with search_items / search_customers and
   use the exact code or name returned. Never make up an item code.
3. If a tool error lists closest matches and exactly one fits what the user said, retry with it yourself.
   Ask the user only if several fit. Never repeat a failing call unchanged.
4. If nothing matches a product, tell the user we don't sell it and create nothing.
5. If a request is ambiguous (for example "wardrobe" without the number of doors, or a name that matches several
   customers), ask the user ONE short question and create nothing yet. If a tool says to ask the user, do that.
6. When the user asks you to draft or raise something and it is not ambiguous, create the draft without asking
   for confirmation; drafts are safe. Report any credit_warning in your answer.
7. If the user asked to submit or confirm, set submit=true on the draft tool (or call submit_document for an
   existing draft). A human must approve; report truthfully whether it was approved. Never submit otherwise.
8. You cannot delete, cancel, pay, edit prices or change existing records. Say plainly that you can't, and
   don't create or submit anything instead.
9. To find who owes the most, call get_customer_dues without a customer.
10. Final answers: short and plain. Include document names and the key numbers from the tool results."""


@dataclass
class RunResult:
    answer: str                                          # what the user sees
    session: str
    model_answer: str = ""                               # the model's own text, before the harness footer
    steps: int = 0
    llm_calls: int = 0
    tool_calls: list = field(default_factory=list)       # {name, args, status, ms}
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    stopped: bool = False                                # hit a budget
    recovered_text_calls: int = 0
    created: dict = field(default_factory=dict)          # document name -> status, created in this run
    messages: list = field(default_factory=list)        # full conversation, for multi-turn use
    messages_user: str = ""                              # the user message this run answered

    @property
    def invalid_calls(self):
        return sum(1 for c in self.tool_calls if c["status"] in ("invalid_args", "unknown_tool", "repeated"))


_JSON_CALL = re.compile(r"^\{\s*\"name\"\s*:\s*\"(\w+)\"\s*,\s*\"arguments\"\s*:\s*(\{.*\})\s*\}$", re.S)
_PY_CALL = re.compile(r"^(\w+)\(((?:\s*\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[\w.\-]+)\s*,?)*)\)$")
_PY_KW = re.compile(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|[\w.\-]+)")


def _calls_from_text(content: str, known: set[str]):
    """Recover a tool call when the WHOLE message is one call printed as text (inside <tool_call> tags, a code
    fence or brackets). Calls mentioned inside prose are not executed."""
    body = (content or "").strip()
    body = re.sub(r"^<tool_call>\s*|\s*</tool_call>$", "", body)
    body = re.sub(r"^```(?:json|python)?\s*|\s*```$", "", body).strip().strip("[]").strip()
    m = _JSON_CALL.match(body)
    if m and m.group(1) in known:
        return [{"id": f"text-{uuid.uuid4().hex[:8]}", "type": "function",
                 "function": {"name": m.group(1), "arguments": m.group(2)}}]
    m = _PY_CALL.match(body)
    if m and m.group(1) in known:
        args = {}
        for k, v in _PY_KW.findall(m.group(2)):
            v = v.strip("\"'")
            args[k] = int(v) if v.isdigit() else v
        return [{"id": f"text-{uuid.uuid4().hex[:8]}", "type": "function",
                 "function": {"name": m.group(1), "arguments": json.dumps(args)}}]
    return []


class Agent:
    def __init__(self, llm: ChatLLM, tools: ERPTools, max_steps=MAX_STEPS, trace=True):
        self.llm, self.tools, self.max_steps = llm, tools, max_steps
        self.trace_path = None
        if trace:
            TRACE_DIR.mkdir(exist_ok=True)
            self.trace_path = TRACE_DIR / f"{tools.session}.jsonl"

    def _trace(self, **event):
        if self.trace_path:
            with self.trace_path.open("a") as f:
                f.write(json.dumps({"t": round(time.time(), 3), **event}, default=str) + "\n")

    @staticmethod
    def _footer(res: RunResult) -> str:
        """Every document created in this run, with its real status, unless the model already named it."""
        asked_to_submit = bool(SUBMIT_WORDS.search(res.messages_user or ""))
        missing = {n: s for n, s in res.created.items() if asked_to_submit or n not in res.model_answer}
        if not missing:
            return ""
        return "\n\nDocuments created (actual status): " + ", ".join(f"{n} ({s})" for n, s in missing.items())

    def _finish(self, res: RunResult, msgs: list, text: str, step: int, stopped=False):
        res.model_answer, res.steps, res.stopped = text, step, stopped
        res.answer = text + self._footer(res)
        msgs.append({"role": "assistant", "content": res.answer})

    def run(self, user_message: str, history: list | None = None) -> RunResult:
        msgs = history[:] if history else [{"role": "system", "content": SYSTEM_PROMPT.format(today=date.today())}]
        msgs.append({"role": "user", "content": user_message})
        self.tools.begin_request(" ".join(m["content"] for m in msgs if m["role"] == "user"), user_message)
        res = RunResult(answer="", session=self.tools.session, messages_user=user_message)
        schemas, known = self.tools.schemas(), set(self.tools.tools)
        seen: dict[str, int] = {}
        t0 = time.monotonic()
        self._trace(event="user", content=user_message, model=self.llm.model)
        for step in range(1, self.max_steps + 1):
            out = self.llm.chat(msgs, schemas)
            res.llm_calls += 1
            res.prompt_tokens += out["prompt_tokens"]
            res.completion_tokens += out["completion_tokens"]
            msg = out["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                calls = _calls_from_text(msg.get("content") or "", known)
                res.recovered_text_calls += len(calls)
            self._trace(event="llm", step=step, latency_s=round(out["latency_s"], 2), content=msg.get("content"),
                        tool_calls=calls)
            if not calls:
                self._finish(res, msgs, (msg.get("content") or "").strip(), step)
                break
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                name, raw = c["function"]["name"], c["function"].get("arguments") or "{}"
                try:
                    key = name + json.dumps(json.loads(raw) if isinstance(raw, str) else raw, sort_keys=True, default=str)
                except json.JSONDecodeError:
                    key = name + str(raw)
                seen[key] = seen.get(key, 0) + 1
                ts = time.monotonic()
                if seen[key] > MAX_SAME_CALL:
                    result, status = {"error": "you already made this exact call; use its earlier result or answer the user"}, "repeated"
                else:
                    result, status = self.tools.execute(name, raw)
                ms = int((time.monotonic() - ts) * 1000)
                res.tool_calls.append({"name": name, "args": raw, "status": status, "ms": ms})
                if status == "ok":
                    for k in ("draft_sales_order", "draft_material_request"):
                        if result.get(k):
                            sub = result.get("submission") or {}
                            res.created[result[k]] = "submitted" if sub.get("submitted") else result.get("status", "draft")
                    if name == "submit_document" and result.get("submitted"):
                        res.created[result["document"]] = "submitted"
                self._trace(event="tool", step=step, name=name, args=raw, status=status, ms=ms, result=result)
                msgs.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": name,
                             "content": json.dumps(result, default=str)})
            if sum(1 for x in res.tool_calls if x["status"] != "ok") >= MAX_FAILED_CALLS:
                self._finish(res, msgs, "I kept getting errors from the ERP tools, so I stopped; nothing was submitted. "
                                        "Please rephrase or give the exact customer and product.", step, stopped=True)
                break
        else:
            self._finish(res, msgs, f"I could not finish this within {self.max_steps} steps; nothing was submitted.",
                         self.max_steps, stopped=True)
        res.latency_s = round(time.monotonic() - t0, 2)
        res.messages = msgs
        self._trace(event="final", answer=res.answer, steps=res.steps, latency_s=res.latency_s, stopped=res.stopped)
        return res
