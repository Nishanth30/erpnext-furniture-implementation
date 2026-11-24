"""Chat with the operations agent in a terminal.

    cd agent && ../.venv/bin/python cli.py                 # local model via Ollama (default qwen2.5:7b)
    LLM_MODEL=qwen2.5:3b ../.venv/bin/python cli.py        # another local model
    LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o-mini LLM_API_KEY=... ../.venv/bin/python cli.py

Submitting a draft always asks you here first (y/N).
"""
import json
import sys
import uuid

from agent import Agent
from erp_api import ERPClient
from llm import ChatLLM
from tools import ERPTools


def approve(summary: str) -> bool:
    return input(f"\n  APPROVAL NEEDED: {summary}. Approve? [y/N] ").strip().lower() == "y"


def main():
    session = f"cli-{uuid.uuid4().hex[:8]}"
    agent = Agent(ChatLLM(), ERPTools(ERPClient(), session=session, approve=approve))
    print(f"Kaveri Furniture Works operations agent ({agent.llm.model}); session {session}. Ctrl-D to quit.")
    history = None
    verbose = "-v" in sys.argv
    while True:
        try:
            text = input("\nyou> ").strip()
        except EOFError:
            print()
            return
        if not text:
            continue
        res = agent.run(text, history)
        history = res.messages
        if verbose:
            for c in res.tool_calls:
                print(f"  [{c['status']}] {c['name']} {json.dumps(c['args']) if not isinstance(c['args'], str) else c['args']} ({c['ms']} ms)")
        print(f"\nagent> {res.answer}\n  ({res.steps} steps, {len(res.tool_calls)} tool calls, {res.latency_s}s)")


if __name__ == "__main__":
    main()
