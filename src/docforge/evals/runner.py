"""Eval runner — orchestrates docforge runs over the testset and scores them.

Workflow per repo:
  1. Resolve local path (clone if git_url, use repo_path if local).
  2. Run docforge end-to-end (scout + index + agent team + critic loop).
  3. For each generated doc, score it on five axes via judge.py.
  4. Aggregate into a RepoScorecard, then write scoreboard_data.json.

We sample factuality calls (cap MAX_FACTUALITY_SAMPLES) so the eval doesn't
spend a fortune of Sonnet 4.6 tokens. Full sampling is opt-in via --full.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..agents import build_graph, initial_state
from ..agents.critic import parse_citations
from ..indexer import build_index
from ..scout import build_manifest
from ..scout.walk import walk_repo
from .judge import (
    JUDGE_MODEL,
    RepoScorecard,
    citation_density,
    judge_completeness,
    judge_coverage_pair,
    judge_factuality_pair,
    judge_readability,
)
from .testset import TestsetEntry


MAX_FACTUALITY_SAMPLES = 12   # citations sampled per repo
MAX_CHUNK_LINES = 80
DEFAULT_TESTSET_DIR = Path("eval/testset")


@dataclass
class RunResult:
    repo: str
    docs: dict[str, str]
    scorecard: RepoScorecard
    duration_sec: float
    errors: list[str] = field(default_factory=list)


def score_repo(
    entry: TestsetEntry,
    *,
    judge_factuality_fn: Callable = judge_factuality_pair,
    judge_coverage_fn: Callable = judge_coverage_pair,
    judge_readability_fn: Callable = judge_readability,
    runner_fn: Callable | None = None,
) -> RunResult:
    """Run docforge against one repo and produce a scorecard.

    `runner_fn` is the dependency injection seam tests use to skip the real
    LangGraph pipeline. By default we call `_run_docforge` which spins up the
    full team graph.
    """
    started = time.time()
    repo_path = _resolve_repo(entry)
    runner_fn = runner_fn or _run_docforge

    drafts, manifest, errors = runner_fn(repo_path)

    # --- Factuality (sample citations) ---
    fact_verdicts = _judge_factuality(drafts, repo_path, judge_factuality_fn)
    fact_score = (
        sum(1 for v in fact_verdicts if v["supported"]) / max(1, len(fact_verdicts))
    )

    # --- Coverage (every ground-truth claim) ---
    cov_verdicts = []
    for claim in entry.claims:
        v = judge_coverage_fn(claim.claim, drafts)
        v["claim_id"] = claim.id
        cov_verdicts.append(v)
    cov_score = (
        sum(1 for v in cov_verdicts if v["found"]) / max(1, len(cov_verdicts))
    )

    # --- Completeness (deterministic) ---
    comp_score, _missing = judge_completeness(drafts, manifest)

    # --- Citation density (deterministic) ---
    cite_density = citation_density(drafts)

    # --- Readability (per-doc, mean) ---
    read_per_doc = {
        name: judge_readability_fn(name, body) for name, body in drafts.items()
    }
    read_scores = [v["score"] for v in read_per_doc.values() if v["score"] > 0]
    read_mean = statistics.mean(read_scores) if read_scores else 0.0

    scorecard = RepoScorecard(
        repo=entry.name,
        factuality=fact_score,
        coverage=cov_score,
        completeness=comp_score,
        citation_density=cite_density,
        readability=read_mean,
        factuality_details=fact_verdicts,
        coverage_details=cov_verdicts,
        readability_details=read_per_doc,
    )

    return RunResult(
        repo=entry.name,
        docs=drafts,
        scorecard=scorecard,
        duration_sec=time.time() - started,
        errors=errors,
    )


def aggregate(results: list[RunResult]) -> dict:
    """Roll up per-repo scorecards into a top-level scoreboard."""
    if not results:
        return {"judge": JUDGE_MODEL, "repos": [], "summary": {}}

    def avg(field: str) -> float:
        vals = [getattr(r.scorecard, field) for r in results]
        return round(statistics.mean(vals), 3) if vals else 0.0

    return {
        "judge": JUDGE_MODEL,
        "n_repos": len(results),
        "summary": {
            "factuality_mean": avg("factuality"),
            "coverage_mean": avg("coverage"),
            "completeness_mean": avg("completeness"),
            "citation_density_mean": avg("citation_density"),
            "readability_mean": avg("readability"),
        },
        "repos": [r.scorecard.to_dict() for r in results],
    }


def write_scoreboard(scoreboard: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(scoreboard, indent=2), encoding="utf-8")


# ---- Internals ----------------------------------------------------------


def _resolve_repo(entry: TestsetEntry) -> Path:
    """Return a local path to the repo, cloning if git_url is given."""
    if entry.repo_path:
        p = Path(entry.repo_path)
        if not p.is_dir():
            raise FileNotFoundError(f"{entry.name}: repo_path missing: {p}")
        return p.resolve()
    if entry.git_url:
        target = Path(tempfile.gettempdir()) / "docforge-eval" / entry.name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth", "1", entry.git_url, str(target)],
                check=True,
            )
        return target
    raise ValueError(f"{entry.name}: no repo_path or git_url")


def _run_docforge(repo: Path) -> tuple[dict[str, str], object, list[str]]:
    """Run scout + index + agent team graph. Returns (drafts, manifest, errors)."""
    manifest = build_manifest(repo)
    files, _ = walk_repo(repo)
    persist = repo / ".docforge" / "chroma"
    index, _ = build_index(repo, files, manifest.repo_name, persist_dir=persist)

    graph = build_graph(index, critic_loop=True)
    state = initial_state(str(repo), manifest.repo_name, manifest, str(repo / ".docforge"))
    final = graph.invoke(state)
    return final.get("drafts", {}) or {}, manifest, list(final.get("errors", []) or [])


def _judge_factuality(
    drafts: dict[str, str], repo_root: Path, judge_fn: Callable,
) -> list[dict]:
    """Pick a representative sample of citations across all docs, judge each."""
    all_refs = []
    for doc_name, body in drafts.items():
        for ref in parse_citations(body):
            all_refs.append((doc_name, ref))

    # Even sampling — interleave docs so we don't overweight README.
    sample: list[tuple[str, object]] = all_refs[:MAX_FACTUALITY_SAMPLES]

    verdicts: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for doc_name, ref in sample:
        key = (ref.file, ref.line_start, ref.line_end)
        if key in seen:
            continue
        seen.add(key)
        path = repo_root / ref.file
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            verdicts.append({
                "claim_id": f"{doc_name}:{ref.file}:{ref.line_start}",
                "supported": False,
                "reason": "file unreadable",
            })
            continue
        lo = max(ref.line_start - 1, 0)
        hi = min(ref.line_end, len(lines))
        if hi - lo > MAX_CHUNK_LINES:
            hi = lo + MAX_CHUNK_LINES
        code = "\n".join(lines[lo:hi])
        line_range = f"{ref.line_start}-{ref.line_end}" if ref.line_end != ref.line_start else str(ref.line_start)
        v = judge_fn(ref.sentence, ref.file, line_range, code)
        v["claim_id"] = f"{doc_name}:{ref.file}:{ref.line_start}"
        verdicts.append(v)
    return verdicts
