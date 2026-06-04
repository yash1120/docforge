"""Architect — synthesizes manifest + module summaries into a structured Architecture.

No retrieval here: the Reader already grounded everything in citations. The
Architect's only job is to roll that up into components + edges that the
Diagrammer and Writer can both lean on.
"""

from __future__ import annotations

from ..llm import Message, chat
from ._utils import extract_json, safe_json_dump
from .state import Architecture, Component, Edge, ExternalDep, GraphState


ARCHITECT_SYSTEM = """You synthesize a software architecture overview from a manifest plus a per-module digest.

Emit ONLY a JSON object — no commentary, no markdown fence. The Diagrammer and Writer downstream
both consume this object directly, so the schema must be exact.

Schema:
{
  "components":  [{"name": "<concise>", "purpose": "<one sentence>",
                   "files": ["repo-relative paths"],
                   "citations": ["file.py:42"]}, ...],
  "edges":       [{"src": "<component name>", "dst": "<component name>", "via": "<short label>"}, ...],
  "external_deps": [{"name": "<external service/lib>", "role": "<one line>"}, ...],
  "runtime_topology": "cli" | "server" | "worker" | "library" | "hybrid"
}

Rules:
1. Component names match exactly between `components`, `edges.src`, `edges.dst`.
2. Don't invent components without evidence in the digest or manifest.
3. Keep `purpose` and `via` to one short line each — they will become diagram labels.
4. Pick a `runtime_topology` that best fits — choose ONE of the five literals."""


ARCHITECT_USER_TEMPLATE = """Repo: {repo_name}  ({primary_language})
Frameworks: {frameworks}
Entry points: {entry_points}
Top-level modules: {modules}
External dep manifests: {dep_files}

Module digest (from Reader):

{summaries_json}

Return the architecture JSON now."""


def run_architect(state: GraphState, *, temperature: float = 0.1) -> dict:
    manifest = state["manifest"]
    summaries = state.get("module_summaries", [])
    errors = list(state.get("errors", []))

    # If reader produced nothing useful, bail with an empty-but-valid architecture.
    if not summaries:
        return {
            "architecture": Architecture(
                components=[], edges=[], external_deps=[],
                runtime_topology="library",
            ),
            "errors": errors + ["architect: no module summaries to synthesize from"],
        }

    user = ARCHITECT_USER_TEMPLATE.format(
        repo_name=manifest.repo_name,
        primary_language=manifest.primary_language or "unknown",
        frameworks=", ".join(manifest.frameworks) or "—",
        entry_points=", ".join(manifest.entry_points) or "—",
        modules=", ".join(manifest.top_level_modules) or "—",
        dep_files=", ".join(manifest.dependency_files) or "—",
        summaries_json=safe_json_dump(summaries),
    )

    raw = chat(
        [
            Message(role="system", content=ARCHITECT_SYSTEM),
            Message(role="user", content=user),
        ],
        temperature=temperature,
        max_tokens=2000,
    )

    try:
        data = extract_json(raw)
    except ValueError as e:
        errors.append(f"architect: failed to parse JSON ({e})")
        return {
            "architecture": Architecture(
                components=[], edges=[], external_deps=[], runtime_topology="library",
            ),
            "errors": errors,
        }

    arch = _normalize(data)
    return {"architecture": arch, "errors": errors}


_VALID_TOPOLOGIES = {"cli", "server", "worker", "library", "hybrid"}


def _normalize(data: dict) -> Architecture:
    """Force the LLM output into the strict TypedDict shape."""
    raw_components = data.get("components") or []
    components: list[Component] = []
    valid_names: set[str] = set()
    for c in raw_components:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        valid_names.add(name)
        components.append(Component(
            name=name,
            purpose=str(c.get("purpose") or "").strip(),
            files=[str(x) for x in (c.get("files") or [])][:20],
            citations=[str(x) for x in (c.get("citations") or [])][:20],
        ))

    raw_edges = data.get("edges") or []
    edges: list[Edge] = []
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        src = str(e.get("src") or e.get("from") or "").strip()
        dst = str(e.get("dst") or e.get("to") or "").strip()
        # Drop edges that reference non-existent components — keeps the diagram clean.
        if not src or not dst or src not in valid_names or dst not in valid_names:
            continue
        edges.append(Edge(src=src, dst=dst, via=str(e.get("via") or "").strip()))

    raw_deps = data.get("external_deps") or []
    deps: list[ExternalDep] = []
    for d in raw_deps:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        deps.append(ExternalDep(name=name, role=str(d.get("role") or "").strip()))

    topo = str(data.get("runtime_topology") or "").strip().lower()
    if topo not in _VALID_TOPOLOGIES:
        topo = "library"

    return Architecture(
        components=components, edges=edges, external_deps=deps, runtime_topology=topo,
    )
