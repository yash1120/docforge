"""Shared agent helpers — JSON extraction, citation formatting, RAG context."""

from __future__ import annotations

import json
import re
from typing import Any


_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def extract_json(text: str) -> Any:
    """Pull a JSON object/array out of an LLM response, tolerating markdown fences.

    Raises ValueError if nothing parses.
    """
    candidates: list[str] = []

    # 1. Fenced code blocks first — most reliable when the prompt asks for them.
    for m in _JSON_FENCE.finditer(text):
        candidates.append(m.group(1).strip())

    # 2. First top-level {...} or [...] in the raw text.
    if obj := _OBJECT_RE.search(text):
        candidates.append(obj.group(0))
    if arr := _ARRAY_RE.search(text):
        candidates.append(arr.group(0))

    # 3. The whole text — sometimes the model just emits raw JSON.
    candidates.append(text.strip())

    last_err: Exception | None = None
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError as e:
            last_err = e
            continue
    raise ValueError(f"No parseable JSON in model output: {last_err}")


def cite(file: str, line_start: int, line_end: int | None = None) -> str:
    """Canonical citation string used across agents: `file:42` or `file:42-58`."""
    if line_end and line_end != line_start:
        return f"{file}:{line_start}-{line_end}"
    return f"{file}:{line_start}"


def hits_to_context(hits: list[dict], max_chars: int = 4000) -> str:
    """Render RAG hits as a prompt-ready context block with explicit citations.

    Each block has a header `### path/to/file.py:42-58 (function/main)` so the
    LLM can copy the citation directly into its output.
    """
    out: list[str] = []
    used = 0
    for h in hits:
        header = f"### {cite(h['file'], h['line_start'], h.get('line_end'))}"
        if h.get("name"):
            header += f"  ({h.get('kind', '?')}/{h['name']})"
        body = h.get("content", "")
        block = f"{header}\n```\n{body}\n```\n"
        if used + len(block) > max_chars and out:
            break
        out.append(block)
        used += len(block)
    return "\n".join(out)


def safe_json_dump(data: Any) -> str:
    """JSON serialize anything, falling back to repr() for non-serializable values."""
    return json.dumps(data, indent=2, default=lambda o: repr(o))
