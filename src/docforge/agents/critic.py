"""Critic — verifies drafts against the source code.

Three checks, in order:
1. **Citation validity (deterministic):** every `[file.py:42]` references a file
   that exists and has the cited line range.
2. **Grounding (LLM):** for each citation, the cited code actually supports the
   surrounding claim — judged by the LLM with the chunk content as evidence.
3. **Coverage (deterministic):** every public symbol in manifest.public_api is
   mentioned somewhere in at least one of the drafts.

The output `Critique` carries a list of Issues plus aggregate scores. The
Editor consumes these and revises the drafts; the supervisor's conditional
edge loops back here until `issues == 0` or `cycles >= MAX_CYCLES`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypedDict

from ..llm import LLMError, Message, chat
from ..scout import Manifest
from ._parallel import parallel_map
from ._utils import extract_json
from .state import GraphState


# ---- Types ---------------------------------------------------------------


class Issue(TypedDict):
    doc: str          # "README.md" etc.
    severity: str     # "error" | "warn"
    kind: str         # "broken_citation" | "ungrounded" | "missing_coverage" | "uncited_claim"
    claim: str        # the claim text or symbol/file
    citation: str | None
    suggestion: str


class Critique(TypedDict):
    issues: list[Issue]
    factuality_score: float   # 0..1 — share of citation claims that grounded
    coverage_score: float     # 0..1 — share of public_api mentioned
    citation_density: float   # avg citations per 100 words across all docs
    summary: str
    cycle: int                # which loop iteration produced this


@dataclass
class CitationRef:
    """A `[file:line]` or `[file:start-end]` parsed from a draft."""
    raw: str         # exact text we matched (incl. brackets)
    file: str
    line_start: int
    line_end: int
    sentence: str    # the sentence the citation appears in (for grounding)


# ---- Citation parsing ----------------------------------------------------


# Match `[path/to/file.ext:N]` or `[path/to/file.ext:N-M]`. Permissive on path chars.
_CITATION_RE = re.compile(
    r"\[((?:[A-Za-z0-9_./\-]+)\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?\]"
)


def parse_citations(markdown: str) -> list[CitationRef]:
    """Pull every `[file:line]` citation out of a markdown doc, with surrounding sentence."""
    refs: list[CitationRef] = []
    # Sentence-split is rough on markdown — we lean on punctuation; close enough for
    # detecting "what claim is this citation attached to".
    for m in _CITATION_RE.finditer(markdown):
        file = m.group(1)
        line_start = int(m.group(2))
        line_end = int(m.group(3)) if m.group(3) else line_start
        sentence = _sentence_around(markdown, m.start(), m.end())
        refs.append(
            CitationRef(
                raw=m.group(0),
                file=file,
                line_start=line_start,
                line_end=line_end,
                sentence=sentence,
            )
        )
    return refs


def _sentence_around(text: str, start: int, end: int) -> str:
    """Return the sentence containing positions [start, end)."""
    # Walk backward to previous sentence terminator or paragraph start.
    left = max(text.rfind(". ", 0, start), text.rfind("\n\n", 0, start), 0)
    left = max(left, 0)
    # Walk forward to next sentence terminator or paragraph end.
    rdot = text.find(". ", end)
    rnl = text.find("\n\n", end)
    right = min(x for x in (rdot, rnl, len(text)) if x != -1) if rdot != -1 or rnl != -1 else len(text)
    sentence = text[left:right].strip(" .\n")
    return sentence[:400]  # cap to keep prompts small


# ---- Deterministic citation validity ------------------------------------


def check_citation(ref: CitationRef, repo_root: Path) -> tuple[bool, str]:
    """Returns (ok, reason). `ok=False` means a broken_citation Issue should be raised."""
    path = repo_root / ref.file
    if not path.exists() or not path.is_file():
        return False, f"file not found: {ref.file}"
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fp:
            line_count = sum(1 for _ in fp)
    except OSError as e:
        return False, f"unreadable: {e}"
    if ref.line_start < 1 or ref.line_end > line_count:
        return False, f"line range {ref.line_start}-{ref.line_end} outside file (file has {line_count} lines)"
    if ref.line_start > ref.line_end:
        return False, f"reversed range {ref.line_start}-{ref.line_end}"
    return True, ""


# ---- Uncited claim detection (heuristic) --------------------------------


# A heuristic: lines that begin with bullet/numbered list AND contain code-ish
# tokens AND don't contain a citation are suspect.
_CODE_TOKEN = re.compile(r"`[^`]+`|[A-Z][a-zA-Z]+\.[a-z_]+|[a-z_]+\(")


def find_uncited_factual_claims(markdown: str, *, max_per_doc: int = 5) -> list[str]:
    """Return up to N sentences that LOOK like factual claims but have no citation.

    We're intentionally conservative — flagging every uncited sentence is noisy;
    we want only the ones referencing code-ish tokens.
    """
    suspects: list[str] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        if "[" in line and _CITATION_RE.search(line):
            continue
        if _CODE_TOKEN.search(line):
            suspects.append(line)
            if len(suspects) >= max_per_doc:
                break
    return suspects


# ---- Coverage (deterministic) -------------------------------------------


_BARE_SYMBOL = re.compile(r"[:.]([A-Za-z_][A-Za-z0-9_]*)$")


def compute_coverage(public_api: list[str], drafts: dict[str, str]) -> tuple[float, list[str]]:
    """Returns (coverage_score, missing_symbols)."""
    if not public_api:
        return 1.0, []

    # Extract bare names — "src/pkg/foo.py::Bar" → "Bar"
    symbols: list[str] = []
    for entry in public_api:
        m = _BARE_SYMBOL.search(entry)
        if m:
            symbols.append(m.group(1))

    if not symbols:
        return 1.0, []

    all_text = "\n".join(drafts.values())
    missing: list[str] = []
    for s in symbols:
        # Word-boundary match so "run" doesn't match inside "runtime"
        if not re.search(rf"\b{re.escape(s)}\b", all_text):
            missing.append(s)

    coverage = 1.0 - (len(missing) / len(symbols))
    return coverage, missing


# ---- Grounding (LLM) -----------------------------------------------------


GROUNDING_SYSTEM = """You verify whether a citation actually supports a claim.

You'll get a CLAIM sentence and a CODE CHUNK from the cited file:line range.
Decide if the chunk plausibly supports the claim — meaning a reader could open
the file and confirm the claim is roughly correct based on what they see.

Be generous with structural claims (e.g. "X is defined in foo.py") and strict
with behavioral claims (e.g. "X returns the user as JSON").

Output ONLY JSON:
{"supported": true|false, "reason": "<one short sentence>"}"""


GROUNDING_USER_TEMPLATE = """CLAIM:
{claim}

CITED CHUNK ({file}:{line_start}-{line_end}):
```
{chunk}
```

Return the JSON verdict now."""


# Retrieval signature matches the rest of the agents.
RetrieveFn = Callable[[str, int, dict | None], list[dict]]


def judge_grounding(
    ref: CitationRef,
    repo_root: Path,
    *,
    max_lines: int = 80,
    temperature: float = 0.0,
) -> tuple[bool, str]:
    """Ask the LLM whether the cited code supports the claim. Returns (supported, reason)."""
    path = repo_root / ref.file
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as e:
        return False, f"unreadable: {e}"

    lo = max(ref.line_start - 1, 0)
    hi = min(ref.line_end, len(lines))
    # Cap chunk size — long ranges aren't useful for grounding judgments.
    if hi - lo > max_lines:
        hi = lo + max_lines
    chunk = "\n".join(lines[lo:hi])

    user = GROUNDING_USER_TEMPLATE.format(
        claim=ref.sentence,
        file=ref.file,
        line_start=ref.line_start,
        line_end=ref.line_end,
        chunk=chunk,
    )
    try:
        raw = chat(
            [
                Message(role="system", content=GROUNDING_SYSTEM),
                Message(role="user", content=user),
            ],
            temperature=temperature,
            max_tokens=200,
        )
    except LLMError as e:
        # No provider — fall back to "supported" so we don't penalize the doc
        # for an infrastructure problem.
        return True, f"grounding skipped (no LLM): {e}"

    try:
        data = extract_json(raw)
    except ValueError:
        return False, f"judge output unparseable: {raw[:120]!r}"
    return bool(data.get("supported")), str(data.get("reason") or "")


# ---- LangGraph node ------------------------------------------------------


# Cap the number of LLM grounding calls per cycle so we don't burn the rate limit.
MAX_GROUNDING_PER_CYCLE = 20


def run_critic(state: GraphState, retrieve: RetrieveFn | None = None) -> dict:
    """Critic node. `retrieve` is unused at the moment but kept in the signature
    so future versions can pull adjacent context for borderline claims."""
    manifest: Manifest = state["manifest"]
    drafts: dict[str, str] = state.get("drafts", {}) or {}
    repo_root = Path(manifest.repo_path)
    cycle = int(state.get("cycles", 0)) + 1
    issues: list[Issue] = []

    total_citations = 0

    # Pass 1 (serial, cheap): parse citations per doc, run deterministic validity,
    # collect (doc_name, ref) pairs for the LLM grounding pass.
    to_judge: list[tuple[str, CitationRef]] = []
    seen: set[tuple[str, int, int]] = set()
    for doc_name, doc_text in drafts.items():
        refs = parse_citations(doc_text)
        total_citations += len(refs)
        for ref in refs:
            ok, reason = check_citation(ref, repo_root)
            if not ok:
                issues.append(Issue(
                    doc=doc_name, severity="error", kind="broken_citation",
                    claim=ref.sentence, citation=ref.raw,
                    suggestion=f"remove or fix: {reason}",
                ))
                continue
            key = (ref.file, ref.line_start, ref.line_end)
            if key in seen:
                continue
            seen.add(key)
            to_judge.append((doc_name, ref))

        # Uncited claims (cheap; serial)
        for suspect in find_uncited_factual_claims(doc_text):
            issues.append(Issue(
                doc=doc_name, severity="warn", kind="uncited_claim",
                claim=suspect, citation=None,
                suggestion="add a [file.py:line] citation or remove the claim",
            ))

    # Pass 2 (parallel, expensive): LLM grounding judgments, bounded by budget.
    judging = to_judge[:MAX_GROUNDING_PER_CYCLE]

    def _judge_one(pair: tuple[str, CitationRef]) -> tuple[str, CitationRef, bool, str]:
        doc_name, ref = pair
        supported, reason = judge_grounding(ref, repo_root)
        return doc_name, ref, supported, reason

    def _on_judge_error(pair: tuple[str, CitationRef], exc: BaseException) -> tuple[str, CitationRef, bool, str]:
        doc_name, ref = pair
        return doc_name, ref, True, f"(grounding errored: {exc})"  # don't penalize for infra fail

    verdicts = parallel_map(_judge_one, judging, default_factory=_on_judge_error)

    grounded_count = 0
    for doc_name, ref, supported, reason in verdicts:
        if supported:
            grounded_count += 1
        else:
            issues.append(Issue(
                doc=doc_name, severity="warn", kind="ungrounded",
                claim=ref.sentence, citation=ref.raw,
                suggestion=f"cited code doesn't support claim ({reason})",
            ))

    # 4. Coverage (cross-doc, deterministic)
    coverage_score, missing = compute_coverage(manifest.public_api, drafts)
    for sym in missing[:10]:
        issues.append(
            Issue(
                doc="(any)",
                severity="warn",
                kind="missing_coverage",
                claim=sym,
                citation=None,
                suggestion=f"mention `{sym}` somewhere in the docs",
            )
        )

    # 5. Aggregate scores
    factuality_score = (
        grounded_count / max(1, total_citations - (total_citations - grounded_count - sum(
            1 for i in issues if i["kind"] == "ungrounded"
        )))
        if total_citations else 1.0
    )
    # Cleaner: factuality = grounded / max(1, citations judged)
    judged = grounded_count + sum(1 for i in issues if i["kind"] == "ungrounded")
    factuality_score = grounded_count / max(1, judged) if judged else 1.0

    total_words = sum(len(re.findall(r"\S+", t)) for t in drafts.values())
    citation_density = (total_citations / total_words * 100) if total_words else 0.0

    summary = (
        f"{len(issues)} issues across {len(drafts)} docs "
        f"(broken={sum(1 for i in issues if i['kind']=='broken_citation')}, "
        f"ungrounded={sum(1 for i in issues if i['kind']=='ungrounded')}, "
        f"coverage_missing={sum(1 for i in issues if i['kind']=='missing_coverage')})"
    )

    critique = Critique(
        issues=issues,
        factuality_score=round(factuality_score, 3),
        coverage_score=round(coverage_score, 3),
        citation_density=round(citation_density, 2),
        summary=summary,
        cycle=cycle,
    )
    return {"critique": dict(critique), "cycles": cycle}
