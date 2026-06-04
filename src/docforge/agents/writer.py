"""Writer — drafts README, ARCHITECTURE, API, TUTORIAL from upstream state.

Hard rule encoded in every prompt: non-trivial claims about behavior end with a
`[file.py:42]` citation. Week 3's Critic enforces this against the RAG store;
the Writer's job is to make sure citations actually appear in the first place.
"""

from __future__ import annotations

import re

from ..llm import Message, chat
from ..scout import Manifest
from ._utils import safe_json_dump
from .state import Architecture, GraphState, ModuleSummary


# Shared citation rule — repeated to every doc prompt so it's never an afterthought.
CITATION_RULE = (
    "Every non-trivial claim about the codebase MUST end with one or more inline "
    "citations like `[path/to/file.py:42]` or `[path/to/file.py:42-58]`. Only use "
    "citations that actually appear in the provided digest/summaries — do not invent. "
    "Trivial sentences (install instructions, generic prose) don't need citations."
)


# ---- README prompt --------------------------------------------------------

README_SYSTEM = f"""You write a clear, honest README for an unfamiliar codebase.

Structure (use these exact `##` headings, in this order):
- (top): one-line description, then a 2-3 sentence pitch
- ## Install
- ## Quickstart
- ## Architecture (one paragraph + the Mermaid block we provide; do NOT modify the diagram)
- ## Modules (bullet list — one per top-level module, with a citation)
- ## License

{CITATION_RULE}

Output ONLY the markdown — no fence around the whole document, no commentary."""


# ---- ARCHITECTURE prompt --------------------------------------------------

ARCH_SYSTEM = f"""You write an ARCHITECTURE.md for an unfamiliar codebase.

Structure:
- (top): one-line "What this is", then the Mermaid diagram block we provide (verbatim)
- ## Components — one `### <name>` subsection per component, with purpose + key files (cited)
- ## Data Flow — describe each edge in prose, with citations to the relevant code
- ## External Dependencies — one bullet per external dep, with role
- ## Runtime Topology — short paragraph: how is this thing actually run/deployed

{CITATION_RULE}

Output ONLY the markdown."""


# ---- API prompt -----------------------------------------------------------

API_SYSTEM = f"""You write an API reference for an unfamiliar codebase.

Structure:
- (top): one-line summary of what's public
- For each top-level module: a `## <module>` heading, then a bullet list of public
  symbols (functions/classes) with one-line descriptions and a citation each.
- If a module has no exposed public API, say so plainly.

{CITATION_RULE}

Output ONLY the markdown. Keep it scannable — table or bullet list, not prose."""


# ---- TUTORIAL prompt ------------------------------------------------------

TUTORIAL_SYSTEM = f"""You write a getting-started TUTORIAL.md for an unfamiliar codebase.

Structure:
- (top): "Who this is for" — one sentence
- ## Setup — prerequisites + install (concrete, copy-pastable)
- ## Your first run — minimum command to make the thing do its thing (cited from CLI/entry point)
- ## Common workflows — 2-3 task-oriented recipes (one ## sub-heading each), each citing the relevant code paths
- ## Troubleshooting — note any obvious gotchas you can see in the code (auth, missing config, etc.)

{CITATION_RULE}

Output ONLY the markdown."""


def run_writer(state: GraphState) -> dict:
    """LangGraph node — drafts all four docs sequentially. Each is independent."""
    manifest: Manifest = state["manifest"]
    summaries: list[ModuleSummary] = state.get("module_summaries", [])
    arch: Architecture = state.get("architecture") or Architecture(
        components=[], edges=[], external_deps=[], runtime_topology="library",
    )
    diagram: str = state.get("diagram_mmd", "")
    errors = list(state.get("errors", []))

    drafts: dict[str, str] = {}
    for name, system_prompt, user_prompt in [
        ("README.md", README_SYSTEM, _readme_user(manifest, summaries, arch, diagram)),
        ("ARCHITECTURE.md", ARCH_SYSTEM, _arch_user(manifest, summaries, arch, diagram)),
        ("API.md", API_SYSTEM, _api_user(manifest, summaries)),
        ("TUTORIAL.md", TUTORIAL_SYSTEM, _tutorial_user(manifest, summaries, arch)),
    ]:
        try:
            text = chat(
                [
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_prompt),
                ],
                temperature=0.2,
                max_tokens=2500,
            )
        except Exception as e:  # noqa: BLE001 — partial drafts are better than no drafts
            errors.append(f"writer[{name}]: {type(e).__name__}: {e}")
            drafts[name] = f"# {name}\n\n_(writer failed: {e})_\n"
            continue
        drafts[name] = _strip_outer_fence(text.strip())

    return {"drafts": drafts, "errors": errors}


# ---- Prompt builders ------------------------------------------------------


def _readme_user(
    manifest: Manifest, summaries: list[ModuleSummary],
    arch: Architecture, diagram: str,
) -> str:
    return f"""Repo: {manifest.repo_name}  ({manifest.primary_language or 'unknown'})
Frameworks: {", ".join(manifest.frameworks) or "—"}
Entry points: {", ".join(manifest.entry_points) or "—"}
License: {manifest.license or "unspecified"}
Has tests: {manifest.has_tests}  CI: {manifest.has_ci}  Docker: {manifest.has_docker}

Existing README (replace this — fix anything contradicted by the code):
{_load_existing_readme(manifest)}

Module digest:
{safe_json_dump(summaries)}

Architecture summary:
{safe_json_dump(arch)}

Mermaid diagram to embed verbatim inside ## Architecture (do NOT modify):
```mermaid
{diagram}
```

Write the README now."""


def _arch_user(
    manifest: Manifest, summaries: list[ModuleSummary],
    arch: Architecture, diagram: str,
) -> str:
    return f"""Repo: {manifest.repo_name}
Primary language: {manifest.primary_language or "unknown"}
Frameworks: {", ".join(manifest.frameworks) or "—"}

Module digest:
{safe_json_dump(summaries)}

Architecture (your structured input — expand into prose):
{safe_json_dump(arch)}

Mermaid diagram to embed at the top (verbatim):
```mermaid
{diagram}
```

Write the ARCHITECTURE.md now."""


def _api_user(manifest: Manifest, summaries: list[ModuleSummary]) -> str:
    public_api_preview = manifest.public_api[:60]
    return f"""Repo: {manifest.repo_name}
Primary language: {manifest.primary_language or "unknown"}

Module digest (use the public_api + citations from each):
{safe_json_dump(summaries)}

Static scout's public-symbol list (additional grounding — may overlap with the digest):
{safe_json_dump(public_api_preview)}

Write the API.md now."""


def _tutorial_user(
    manifest: Manifest, summaries: list[ModuleSummary], arch: Architecture
) -> str:
    return f"""Repo: {manifest.repo_name}
Primary language: {manifest.primary_language or "unknown"}
Frameworks: {", ".join(manifest.frameworks) or "—"}
Entry points: {", ".join(manifest.entry_points) or "—"}
Dependency files: {", ".join(manifest.dependency_files) or "—"}
Has tests: {manifest.has_tests}  CI: {manifest.has_ci}  Docker: {manifest.has_docker}

Module digest:
{safe_json_dump(summaries)}

Runtime topology hint: {arch["runtime_topology"]}

Write the TUTORIAL.md now."""


# ---- Helpers --------------------------------------------------------------


_OUTER_FENCE = re.compile(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", re.DOTALL)


def _strip_outer_fence(text: str) -> str:
    """If the LLM wrapped the WHOLE doc in a code fence, peel it off."""
    m = _OUTER_FENCE.match(text)
    return m.group(1) if m else text


def _load_existing_readme(manifest: Manifest) -> str:
    if not manifest.readme_path:
        return "_(no existing README)_"
    from pathlib import Path

    p = Path(manifest.repo_path) / manifest.readme_path
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "_(could not read existing README)_"
    if len(text) > 5000:
        text = text[:5000] + "\n# ...truncated\n"
    return text
