"""Minimal client for any OpenAI-compatible chat-completions endpoint with tool calling.

Default: a local model served by Ollama (no API key, no cost). Point LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
at OpenAI or any compatible provider to run the same agent and evaluation on a hosted model.
"""
import os
import time

import requests


class LLMError(Exception):
    pass


class ChatLLM:
    def __init__(self, model=None, base_url=None, api_key=None, temperature=0.0, seed=None, timeout=300):
        self.model = model or os.environ.get("LLM_MODEL", "qwen2.5:7b")
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "ollama")
        self.temperature, self.seed, self.timeout = temperature, seed, timeout

    def chat(self, messages, tools=None):
        body = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            body["tools"] = tools
        if self.seed is not None:
            body["seed"] = self.seed
        t0 = time.monotonic()
        try:
            r = requests.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}"})
        except requests.RequestException as e:
            raise LLMError(f"LLM endpoint unreachable: {e}") from e
        if r.status_code >= 400:
            raise LLMError(f"LLM {r.status_code}: {r.text[:300]}")
        data = r.json()
        usage = data.get("usage") or {}
        return {"message": data["choices"][0]["message"], "latency_s": time.monotonic() - t0,
                "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0)}
