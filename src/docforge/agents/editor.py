"""Editor — applies Critic feedback to drafts.

Per doc with open issues: build a "here's your draft + here's what's wrong"
prompt and ask the LLM to emit a revised draft. Skip docs that the Critic
flagged with zero issues.
"""

from __future__ import annotations

from ..llm import LLMError, Message, chat
from ._utils import safe_json_dump
from .state import GraphState


EDITOR_SYSTEM = """You revise documentation based on a list of issues.

Rules:
1. Apply each issue's suggestion exactly. Remove or correct claims; add missing citations from the provided RAG context; mention required symbols.
2. Citations remain in `[path/to/file.py:42]` or `[path/to/file.py:42-58]` form. Only use citations grounded in the original draft, the issue list, or the RAG hints.
3. Preserve the doc's structure (headings, ordering) unless an issue tells you to change it.
4. Output ONLY the revised markdown for this one document — no commentary, no outer fence."""


EDITOR_USER_TEMPLATE = """Document: {doc}

Current draft:
{draft}

Open issues to address ({n_issues}):
{issues_json}

Return the revised markdown now."""


def run_editor(state: GraphState) -> dict:
    """Editor node. Revises every doc that has at least one Critic issue."""
    drafts: dict[str, str] = dict(state.get("drafts", {}) or {})
    critique: dict = state.get("critique") or {}
    issues: list[dict] = critique.get("issues") or []
    errors = list(state.get("errors", []))

    if not issues:
        # Nothing to revise; pass-through.
        return {"drafts": drafts, "errors": errors}

    # Group issues by doc. Issues marked doc="(any)" apply across all docs —
    # we attach them to every doc so the Editor can decide where to address them.
    by_doc: dict[str, list[dict]] = {name: [] for name in drafts}
    floating: list[dict] = []
    for issue in issues:
        target = issue.get("doc", "")
        if target in by_doc:
            by_doc[target].append(issue)
        else:
            floating.append(issue)
    for name in by_doc:
        by_doc[name].extend(floating)

    new_drafts: dict[str, str] = {}
    for name, body in drafts.items():
        doc_issues = by_doc.get(name, [])
        if not doc_issues:
            new_drafts[name] = body
            continue

        user = EDITOR_USER_TEMPLATE.format(
            doc=name,
            draft=body,
            n_issues=len(doc_issues),
            issues_json=safe_json_dump(doc_issues),
        )
        try:
            revised = chat(
                [
                    Message(role="system", content=EDITOR_SYSTEM),
                    Message(role="user", content=user),
                ],
                temperature=0.1,
                max_tokens=2500,
            )
        except LLMError as e:
            errors.append(f"editor[{name}]: {e}")
            new_drafts[name] = body
            continue
        except Exception as e:  # noqa: BLE001 — partial recovery is better than total failure
            errors.append(f"editor[{name}]: {type(e).__name__}: {e}")
            new_drafts[name] = body
            continue

        new_drafts[name] = revised.strip()

    return {"drafts": new_drafts, "errors": errors}
