"""
LLM provider abstraction.

Providers implement embed() and generate(). generate() runs the full tool-calling
loop internally so provider-specific message formats stay encapsulated. The vendor
is chosen by settings.LLM_PROVIDER; a Mock provider runs fully offline (no key) and
is the default, so dev and tests never require network access.
"""
import hashlib
import json
import math
import urllib.request
import json
import urllib.request
from typing import Generator

from django.conf import settings


# ---------------------------------------------------------------- HTTP helper
def _post_json(url, payload, api_key):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------- base
class LLMProvider:
    def embed(self, texts):
        raise NotImplementedError

    def generate(self, *, system, messages, tools, tool_executor):
        raise NotImplementedError


# ---------------------------------------------------------------- OpenAI
class OpenAIProvider(LLMProvider):
    BASE = "https://api.openai.com/v1"

    def embed(self, texts):
        resp = _post_json(
            f"{self.BASE}/embeddings",
            {"model": settings.OPENAI_EMBED_MODEL, "input": texts},
            settings.LLM_API_KEY,
        )
        return [d["embedding"] for d in resp["data"]]

    def generate(self, *, system, messages, tools, tool_executor):
        oai = [{"role": "system", "content": system}]
        oai += [{"role": m["role"], "content": m["content"]} for m in messages]

        for _ in range(settings.AI_MAX_TOOL_ROUNDS):
            resp = _post_json(
                f"{self.BASE}/chat/completions",
                {"model": settings.OPENAI_CHAT_MODEL, "messages": oai,
                 "tools": tools, "tool_choice": "auto"},
                settings.LLM_API_KEY,
            )
            msg = resp["choices"][0]["message"]
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                return msg.get("content") or ""
            oai.append(msg)  # assistant turn carrying the tool calls
            for tc in tool_calls:
                args = json.loads(tc["function"].get("arguments") or "{}")
                result = tool_executor(tc["function"]["name"], args)
                oai.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        return "I wasn't able to complete that request."
    
    def generate_stream(self, *, system, messages, tools, tool_executor) -> Generator[str, None, None]:
        oai = [{"role": "system", "content": system}]
        oai += [{"role": m["role"], "content": m["content"]} for m in messages]

        # We'll do a single round (no tool loops) for streaming simplicity.
        # If tools are called, we fall back to non‑streaming for that turn.
        # For production, you'd want a full streaming tool‑loop; but to keep it simple, we just run once.
        resp = self._post_json_stream(
            f"{self.BASE}/chat/completions",
            {"model": settings.OPENAI_CHAT_MODEL, "messages": oai,
             "tools": tools, "tool_choice": "auto", "stream": True},
            settings.LLM_API_KEY,
        )
        # Yield each chunk
        for chunk in resp:
            try:
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content
                # If tool_calls appear, we'd need to handle them, but we skip for simplicity
            except (KeyError, IndexError):
                pass

    def _post_json_stream(self, url, payload, api_key):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            for line in resp:
                line = line.decode().strip()
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    yield json.loads(data)


# ---------------------------------------------------------------- Mock (offline)
class MockProvider(LLMProvider):
    """Deterministic, network-free. Good enough for dev + tests."""

    DIM = 64

    def embed(self, texts):
        return [self._vec(t) for t in texts]

    def _vec(self, text):
        v = [0.0] * self.DIM
        for word in text.lower().split():
            # Stable (process-independent) bucket, unlike builtin hash().
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.DIM
            v[bucket] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def generate(self, *, system, messages, tools, tool_executor):
        last = (messages[-1]["content"].lower() if messages else "")
        if tools and "booking" in last:
            return f"Based on your bookings: {tool_executor('get_my_bookings', {})}"
        if tools and ("application" in last or "job" in last):
            return f"Based on your applications: {tool_executor('get_my_applications', {})}"
        return "Hello! I'm the JobCatch assistant. Ask me about your bookings or applications."

    def generate_stream(self, *, system, messages, tools, tool_executor) -> Generator[str, None, None]:
        # Simulate streaming with word-by-word
        reply = self.generate(system=system, messages=messages, tools=tools, tool_executor=tool_executor)
        for word in reply.split():
            yield word + " "
            import time; time.sleep(0.05)   # simulate latency


def get_provider():
    if settings.LLM_PROVIDER == "openai" and settings.LLM_API_KEY:
        return OpenAIProvider()
    return MockProvider()
