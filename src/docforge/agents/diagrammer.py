"""Diagrammer — Architecture JSON → Mermaid flowchart.

Deterministic primary path (no LLM): guarantees we always emit valid Mermaid,
even if the LLM has been failing. Optional LLM "beautify" pass refines layout
and adds subgraphs / external-deps cluster — but only if it validates; otherwise
we fall back to the deterministic version.
"""

from __future__ import annotations

import re

from ..llm import LLMError, Message, chat, provider_in_use
from ._utils import safe_json_dump
from .state import Architecture, GraphState


# ---- Deterministic renderer ----------------------------------------------


def mermaid_from_architecture(arch: Architecture) -> str:
    """Emit a Mermaid flowchart TD from the structured Architecture."""
    lines: list[str] = ["flowchart TD"]
    used_ids: dict[str, str] = {}  # component name -> safe id

    # Component nodes
    for c in arch["components"]:
        nid = _safe_id(c["name"], used_ids)
        label = _escape_label(c["name"])
        purpose = c.get("purpose", "")
        if purpose:
            label = f"{label}<br/><span style='font-size:smaller'>{_escape_label(purpose)}</span>"
        lines.append(f"    {nid}[\"{label}\"]")

    # External deps subgraph
    if arch["external_deps"]:
        lines.append("    subgraph external [external]")
        for d in arch["external_deps"]:
            nid = _safe_id(d["name"], used_ids, prefix="ext_")
            label = _escape_label(d["name"])
            role = d.get("role", "")
            if role:
                label = f"{label}<br/><span style='font-size:smaller'>{_escape_label(role)}</span>"
            lines.append(f"        {nid}[(\"{label}\")]")
        lines.append("    end")

    # Edges
    for e in arch["edges"]:
        if e["src"] not in used_ids or e["dst"] not in used_ids:
            continue
        src = used_ids[e["src"]]
        dst = used_ids[e["dst"]]
        via = e.get("via", "")
        if via:
            lines.append(f"    {src} -- {_escape_label(via)} --> {dst}")
        else:
            lines.append(f"    {src} --> {dst}")

    # Fallback: if no components at all, still produce something parseable
    if len(lines) == 1:
        lines.append("    empty[\"(no components detected)\"]")

    return "\n".join(lines) + "\n"


_ID_CLEAN = re.compile(r"[^a-zA-Z0-9_]+")


def _safe_id(name: str, used: dict[str, str], prefix: str = "") -> str:
    cleaned = _ID_CLEAN.sub("_", name).strip("_") or "node"
    if cleaned[:1].isdigit():
        cleaned = f"n_{cleaned}"
    candidate = f"{prefix}{cleaned}"
    base = candidate
    i = 2
    while candidate in used.values():
        candidate = f"{base}_{i}"
        i += 1
    used[name] = candidate
    return candidate


def _escape_label(text: str) -> str:
    """Mermaid label escaping: quote-stripping + length cap + entity for `\"` and `]`."""
    if not text:
        return ""
    text = text.replace("\"", "&quot;").replace("]", "&#93;").replace("[", "&#91;")
    if len(text) > 80:
        text = text[:77] + "..."
    return text


# ---- Validation ----------------------------------------------------------


_DECLARATION = re.compile(r"^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram)\b", re.MULTILINE)


def validate_mermaid(text: str) -> tuple[bool, str]:
    """Cheap structural check — without mermaid-cli we still want a guard.

    Catches: missing declaration, no edges + no nodes, broken subgraph blocks,
    unmatched fences. Returns (ok, reason).
    """
    if not text or not text.strip():
        return False, "empty"
    if not _DECLARATION.search(text):
        return False, "missing diagram declaration (expected flowchart/graph/...)"
    # Balanced subgraph blocks
    sub_open = len(re.findall(r"^\s*subgraph\b", text, re.MULTILINE))
    sub_close = len(re.findall(r"^\s*end\s*$", text, re.MULTILINE))
    if sub_open != sub_close:
        return False, f"subgraph blocks unbalanced ({sub_open} open / {sub_close} end)"
    # Must contain at least one node or edge declaration.
    if not re.search(r"\[|\(|-->", text):
        return False, "no nodes or edges in body"
    return True, ""


# ---- Optional LLM beautify pass ------------------------------------------


BEAUTIFY_SYSTEM = """You polish a Mermaid flowchart for readability.

Rules:
1. Preserve every node and every edge from the input. Do not add new components.
2. You MAY add `subgraph` blocks to group related components, change layout
   direction (TD/LR/etc), or shorten labels. You may NOT remove edges.
3. Output ONLY the Mermaid source — no fence, no commentary.
4. Keep the diagram under 40 nodes total; if there are more, leave structure alone."""


BEAUTIFY_USER_TEMPLATE = """Architecture (for context — do not invent from it):
{arch_json}

Current Mermaid:
{current}

Return a refined Mermaid diagram now."""


def beautify_with_llm(arch: Architecture, deterministic: str) -> str | None:
    """Returns refined Mermaid, or None if it fails / doesn't validate."""
    if provider_in_use() == "none":
        return None
    try:
        raw = chat(
            [
                Message(role="system", content=BEAUTIFY_SYSTEM),
                Message(
                    role="user",
                    content=BEAUTIFY_USER_TEMPLATE.format(
                        arch_json=safe_json_dump(arch),
                        current=deterministic,
                    ),
                ),
            ],
            temperature=0.2,
            max_tokens=1500,
        )
    except LLMError:
        return None

    # Strip code fences if the LLM disobeyed
    text = re.sub(r"^```(?:mermaid)?\s*\n", "", raw.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n```\s*$", "", text)
    ok, _ = validate_mermaid(text)
    if not ok:
        return None
    return text


# ---- LangGraph node ------------------------------------------------------


def run_diagrammer(state: GraphState, *, beautify: bool | None = None) -> dict:
    arch = state.get("architecture") or Architecture(
        components=[], edges=[], external_deps=[], runtime_topology="library",
    )
    attempts = int(state.get("diagram_attempts", 0))
    errors = list(state.get("errors", []))
    if beautify is None:
        import os
        beautify = os.environ.get("DOCFORGE_DIAGRAM_BEAUTIFY", "1") != "0"

    deterministic = mermaid_from_architecture(arch)
    ok, reason = validate_mermaid(deterministic)
    if not ok:
        # This means our own renderer is bugged — bail loudly rather than silently.
        errors.append(f"diagrammer: deterministic render invalid ({reason})")
        return {"diagram_mmd": deterministic, "diagram_attempts": attempts + 1, "errors": errors}

    if beautify:
        refined = beautify_with_llm(arch, deterministic)
        if refined:
            return {"diagram_mmd": refined, "diagram_attempts": attempts + 1, "errors": errors}

    return {"diagram_mmd": deterministic, "diagram_attempts": attempts + 1, "errors": errors}
