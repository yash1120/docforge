"""Reader — one summary per top-level module, grounded in RAG retrieval.

For each module in the manifest, the Reader runs a small bundle of queries against
the code-RAG store ("what does this module do", "public functions", "external
deps") and writes a structured ModuleSummary that downstream agents lean on.
"""

from __future__ import annotations

from typing import Callable

from ..llm import Message, chat
from ..scout import Manifest
from ._utils import cite, extract_json, hits_to_context
from .state import GraphState, ModuleSummary


READER_SYSTEM = """You are a senior engineer reading a module of an unfamiliar codebase.

Your job: produce a strict JSON object describing this one module.

Rules:
1. Every claim in "key_behaviors" MUST end with one or more inline citations like `[path/to/file.py:42]` or `[path/to/file.py:42-58]`. Use the citations exactly as they appear in the context blocks.
2. Do not invent files, functions, or behavior not present in the context. If you can't see evidence, say so or omit the claim.
3. Output ONLY a JSON object — no commentary, no markdown fence.

Schema:
{
  "module": "<repo-relative path you were asked about>",
  "purpose": "<one or two sentence what this module does>",
  "public_api": ["<symbol or short signature>", ...],
  "key_behaviors": ["<behavior with at least one [file:line] citation>", ...],
  "citations": ["<file:line or file:start-end strings collected from the above>", ...]
}"""


READER_USER_TEMPLATE = """Module to summarize: `{module}`

Repo: {repo_name}  ({primary_language})
Frameworks in use: {frameworks}

Retrieved code chunks (use these as your ONLY source of truth):

{context}

Return the JSON object now."""


# Queries we run per module — bget the top-k chunks for each, dedupe by id.
MODULE_QUERIES = [
    "what this module does, purpose and responsibilities",
    "public functions and classes defined here",
    "external dependencies and how they're used",
    "main entry point or public API surface",
]
PER_QUERY_K = 5
MAX_HITS = 12


# Retrieval signature: query: str, k: int, where: dict | None -> list[dict]
RetrieveFn = Callable[[str, int, dict | None], list[dict]]


def read_module(
    module: str,
    manifest: Manifest,
    retrieve: RetrieveFn,
    *,
    temperature: float = 0.1,
) -> ModuleSummary:
    """Run the Reader against one module. Returns a ModuleSummary."""
    prefix = _module_prefix(module)
    seen_ids: set[str] = set()
    hits: list[dict] = []
    # Over-fetch so we have headroom after the client-side prefix filter.
    fetch_k = PER_QUERY_K * 3 if prefix else PER_QUERY_K
    for q in MODULE_QUERIES:
        for h in retrieve(q, fetch_k, None):
            if prefix and not h["file"].startswith(prefix):
                continue
            key = f"{h['file']}:{h['line_start']}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            hits.append(h)
            if len(hits) >= MAX_HITS:
                break
        if len(hits) >= MAX_HITS:
            break

    if not hits:
        # Empty RAG return — surface honestly, don't make the LLM hallucinate.
        return ModuleSummary(
            module=module,
            purpose="(no indexed content found for this module)",
            public_api=[],
            key_behaviors=[],
            citations=[],
        )

    context = hits_to_context(hits, max_chars=6000)
    user = READER_USER_TEMPLATE.format(
        module=module,
        repo_name=manifest.repo_name,
        primary_language=manifest.primary_language or "unknown",
        frameworks=", ".join(manifest.frameworks) or "—",
        context=context,
    )

    raw = chat(
        [
            Message(role="system", content=READER_SYSTEM),
            Message(role="user", content=user),
        ],
        temperature=temperature,
        max_tokens=1800,
    )

    try:
        data = extract_json(raw)
    except ValueError:
        return ModuleSummary(
            module=module,
            purpose=f"(reader failed to parse JSON; raw head: {raw[:200]!r})",
            public_api=[],
            key_behaviors=[],
            citations=[cite(h["file"], h["line_start"], h.get("line_end")) for h in hits[:3]],
        )

    # Defensive normalization — LLMs sometimes diverge from schema.
    return ModuleSummary(
        module=str(data.get("module") or module),
        purpose=str(data.get("purpose") or "").strip(),
        public_api=[str(x) for x in (data.get("public_api") or [])][:25],
        key_behaviors=[str(x) for x in (data.get("key_behaviors") or [])][:15],
        citations=[str(x) for x in (data.get("citations") or [])][:25],
    )


def _module_prefix(module: str) -> str:
    """Return a forward-slashed module path used for client-side hit filtering.

    Chroma's `where` doesn't support regex/prefix on metadata, so we post-filter
    by file path against this prefix. Empty string means "no filter".
    """
    if not module or module == "/" or module == ".":
        return ""
    return module.replace("\\", "/").rstrip("/") + "/"


def run_reader(state: GraphState, retrieve: RetrieveFn) -> dict:
    """LangGraph node — reads every top-level module, returns updates."""
    manifest: Manifest = state["manifest"]
    modules = manifest.top_level_modules or [""]   # "" means whole repo

    summaries: list[ModuleSummary] = []
    errors: list[str] = list(state.get("errors", []))
    for mod in modules:
        try:
            summaries.append(read_module(mod, manifest, retrieve))
        except Exception as e:  # noqa: BLE001 — never let one bad module kill the graph
            errors.append(f"reader[{mod}]: {type(e).__name__}: {e}")
            summaries.append(
                ModuleSummary(
                    module=mod, purpose=f"(reader errored: {e})",
                    public_api=[], key_behaviors=[], citations=[],
                )
            )
    return {"module_summaries": summaries, "errors": errors}
