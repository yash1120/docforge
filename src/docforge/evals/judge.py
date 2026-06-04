"""LLM-as-judge for the eval scoreboard.

Plan locks **Claude Sonnet 4.6** as the judge model — strictly for offline
grading, not serving. We force `model="claude-sonnet-4-6"` on every judge
call so the runner can't accidentally use a cheaper model and inflate scores.

Five axes (per the plan §7):
    factuality   — share of cited claims whose code actually supports them
    coverage     — share of ground-truth claims surfaced in the generated docs
    completeness — does the doc mention entry point / install / deps / license?
    citation_density — citations per 100 words (deterministic; not LLM-judged)
    readability  — judge rates prose 1-5
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TypedDict

from ..agents._utils import extract_json
from ..agents.critic import parse_citations
from ..llm import LLMError, Message, chat
from ..scout import Manifest


JUDGE_MODEL = "claude-sonnet-4-6"


# ---- Per-axis return shapes ---------------------------------------------


class FactualityVerdict(TypedDict):
    claim_id: str
    supported: bool
    reason: str


class CoverageVerdict(TypedDict):
    claim_id: str
    found: bool
    where: str       # which doc + a short snippet
    reason: str


class ReadabilityVerdict(TypedDict):
    score: int          # 1..5
    rationale: str


@dataclass
class RepoScorecard:
    repo: str
    factuality: float = 0.0
    coverage: float = 0.0
    completeness: float = 0.0
    citation_density: float = 0.0
    readability: float = 0.0
    factuality_details: list[FactualityVerdict] = field(default_factory=list)
    coverage_details: list[CoverageVerdict] = field(default_factory=list)
    readability_details: dict[str, ReadabilityVerdict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "factuality": round(self.factuality, 3),
            "coverage": round(self.coverage, 3),
            "completeness": round(self.completeness, 3),
            "citation_density": round(self.citation_density, 2),
            "readability": round(self.readability, 2),
            "factuality_details": self.factuality_details,
            "coverage_details": self.coverage_details,
            "readability_details": self.readability_details,
        }


# ---- Factuality ---------------------------------------------------------


FACTUALITY_SYSTEM = """You judge whether a citation in a generated documentation file actually supports its surrounding claim.

You'll get:
- CLAIM: the sentence in the docs
- CODE: the lines from the cited file:line range

Decide: could a reader, opening the file at that line range, reasonably confirm the claim is true?

Be generous with structural claims ("X is defined in foo.py at line 42"), strict with behavioral claims ("X returns a JSON response").

Output ONLY JSON: {"supported": true|false, "reason": "<one sentence>"}"""


def judge_factuality_pair(claim_text: str, file_path: str, line_range: str, code: str) -> FactualityVerdict:
    """One factuality judgment. Returns a FactualityVerdict; never raises."""
    user = (
        f"CLAIM:\n{claim_text}\n\n"
        f"CODE ({file_path}:{line_range}):\n```\n{code}\n```\n\n"
        f"Return the JSON verdict now."
    )
    try:
        raw = chat(
            [
                Message(role="system", content=FACTUALITY_SYSTEM),
                Message(role="user", content=user),
            ],
            model=JUDGE_MODEL,
            temperature=0.0,
            max_tokens=200,
        )
    except LLMError as e:
        return FactualityVerdict(claim_id="", supported=False, reason=f"judge call failed: {e}")

    try:
        data = extract_json(raw)
    except ValueError:
        return FactualityVerdict(claim_id="", supported=False, reason=f"unparseable judge output: {raw[:120]!r}")
    return FactualityVerdict(
        claim_id="",
        supported=bool(data.get("supported")),
        reason=str(data.get("reason") or ""),
    )


# ---- Coverage -----------------------------------------------------------


COVERAGE_SYSTEM = """You decide whether a ground-truth claim about a codebase is reflected somewhere in the generated documentation.

Be charitable — paraphrases count. The claim doesn't need to appear verbatim; it just needs to be conveyed.

Output ONLY JSON:
{"found": true|false, "where": "<doc name + short snippet, or empty>", "reason": "<one sentence>"}"""


def judge_coverage_pair(claim_text: str, drafts: dict[str, str]) -> CoverageVerdict:
    blob = "\n\n---\n\n".join(f"# {n}\n{b}" for n, b in drafts.items())
    # Trim very large concatenations so the judge call stays small.
    if len(blob) > 30_000:
        blob = blob[:30_000] + "\n# ...truncated\n"

    user = f"GROUND-TRUTH CLAIM:\n{claim_text}\n\nGENERATED DOCS:\n{blob}\n\nReturn the JSON verdict now."
    try:
        raw = chat(
            [
                Message(role="system", content=COVERAGE_SYSTEM),
                Message(role="user", content=user),
            ],
            model=JUDGE_MODEL,
            temperature=0.0,
            max_tokens=300,
        )
    except LLMError as e:
        return CoverageVerdict(claim_id="", found=False, where="", reason=f"judge call failed: {e}")

    try:
        data = extract_json(raw)
    except ValueError:
        return CoverageVerdict(claim_id="", found=False, where="", reason=f"unparseable judge output: {raw[:120]!r}")
    return CoverageVerdict(
        claim_id="",
        found=bool(data.get("found")),
        where=str(data.get("where") or ""),
        reason=str(data.get("reason") or ""),
    )


# ---- Completeness (deterministic) ---------------------------------------


_INSTALL_RE = re.compile(r"\b(pip install|npm install|cargo install|go install|poetry add)\b", re.IGNORECASE)
_RUN_RE = re.compile(r"\b(usage|quickstart|getting started|first run|run)\b", re.IGNORECASE)
_LICENSE_RE = re.compile(r"\b(licen[cs]e)\b", re.IGNORECASE)


def judge_completeness(drafts: dict[str, str], manifest: Manifest) -> tuple[float, list[str]]:
    """Returns (score in 0..1, list of missing aspects)."""
    blob = "\n".join(drafts.values())
    blob_lower = blob.lower()
    checks: list[tuple[str, bool]] = [
        ("entry_point", any(ep.lower().split("/")[-1] in blob_lower for ep in manifest.entry_points)),
        ("install_command", bool(_INSTALL_RE.search(blob))),
        ("run_instruction", bool(_RUN_RE.search(blob))),
        ("license_mention", bool(_LICENSE_RE.search(blob)) if manifest.license else True),
        ("dependency_mention", any(
            dep.lower() in blob_lower
            for dep_list in manifest.dependencies.values()
            for dep in (dep_list or [])[:5]
        ) if manifest.dependencies else True),
    ]
    passed = [name for name, ok in checks if ok]
    missing = [name for name, ok in checks if not ok]
    score = len(passed) / len(checks)
    return score, missing


# ---- Citation density (deterministic) -----------------------------------


def citation_density(drafts: dict[str, str]) -> float:
    """Citations per 100 words across all docs."""
    blob = "\n".join(drafts.values())
    words = len(re.findall(r"\S+", blob))
    cites = len(parse_citations(blob))
    return (cites / words * 100) if words else 0.0


# ---- Readability --------------------------------------------------------


READABILITY_SYSTEM = """You rate the readability of a technical documentation file on a 1-5 scale.

Rubric:
1 = unreadable, broken structure, contradictions
2 = readable but heavy issues (missing context, jargon, no structure)
3 = readable but bland; could onboard a reader with effort
4 = clear, well-structured, useful examples; minor issues
5 = excellent — concise, accurate, well-formatted, easy to follow

Be honest. Output ONLY JSON: {"score": 1..5, "rationale": "<one sentence>"}"""


def judge_readability(doc_name: str, doc_text: str) -> ReadabilityVerdict:
    user = f"DOCUMENT: {doc_name}\n\n{doc_text[:8000]}\n\nRate it 1-5."
    try:
        raw = chat(
            [
                Message(role="system", content=READABILITY_SYSTEM),
                Message(role="user", content=user),
            ],
            model=JUDGE_MODEL,
            temperature=0.0,
            max_tokens=200,
        )
    except LLMError as e:
        return ReadabilityVerdict(score=0, rationale=f"judge call failed: {e}")

    try:
        data = extract_json(raw)
    except ValueError:
        return ReadabilityVerdict(score=0, rationale=f"unparseable judge output: {raw[:120]!r}")

    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    return ReadabilityVerdict(
        score=max(0, min(5, score)),
        rationale=str(data.get("rationale") or ""),
    )
