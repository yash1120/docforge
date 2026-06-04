"""Tiny provider-agnostic chat client.

Prefers Groq (plan-locked: Llama 3.3 70B + Qwen 2.5 Coder). Falls back to
Anthropic if GROQ_API_KEY is missing — useful while Groq isn't set up.

Why not LangChain here? At Week 1 we just need `chat(messages) -> str`. We'll
introduce a LangGraph supervisor in Week 2 once there's more than one agent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

load_dotenv()  # quietly pick up .env when present

Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str


# Groq model IDs the plan locks in.
GROQ_GENERAL = "llama-3.3-70b-versatile"
GROQ_CODE = "qwen-2.5-coder-32b"

# Anthropic fallback for when Groq isn't configured.
ANTHROPIC_FALLBACK = "claude-sonnet-4-6"


class LLMError(RuntimeError):
    """Raised when no provider is configured or the call fails."""


def chat(
    messages: list[Message],
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """Synchronous chat call. Returns the assistant's text response.

    Provider selection:
      1. GROQ_API_KEY set → Groq (use `model` arg or GROQ_GENERAL).
      2. ANTHROPIC_API_KEY set → Anthropic (Sonnet 4.6).
      3. Neither → LLMError.
    """
    if os.environ.get("GROQ_API_KEY"):
        return _groq_chat(messages, model or GROQ_GENERAL, temperature, max_tokens)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _anthropic_chat(messages, ANTHROPIC_FALLBACK, temperature, max_tokens)
    raise LLMError(
        "No LLM provider configured. Set GROQ_API_KEY (preferred) or ANTHROPIC_API_KEY."
    )


def provider_in_use() -> str:
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "none"


def _groq_chat(
    messages: list[Message], model: str, temperature: float, max_tokens: int
) -> str:
    from groq import Groq

    client = Groq()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": m.role, "content": m.content} for m in messages],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _anthropic_chat(
    messages: list[Message], model: str, temperature: float, max_tokens: int
) -> str:
    from anthropic import Anthropic

    client = Anthropic()
    system = next((m.content for m in messages if m.role == "system"), None)
    non_system = [
        {"role": m.role, "content": m.content} for m in messages if m.role != "system"
    ]
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": non_system,
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    # Concatenate text blocks; Anthropic returns a content list.
    parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)
