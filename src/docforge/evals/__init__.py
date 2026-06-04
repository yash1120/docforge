"""docforge eval harness.

Public API:
    from docforge.evals import load_testset, score_repo, aggregate, write_scoreboard
"""

from .judge import (
    JUDGE_MODEL,
    RepoScorecard,
    citation_density,
    judge_completeness,
    judge_coverage_pair,
    judge_factuality_pair,
    judge_readability,
)
from .runner import (
    DEFAULT_TESTSET_DIR,
    MAX_FACTUALITY_SAMPLES,
    RunResult,
    aggregate,
    score_repo,
    write_scoreboard,
)
from .testset import GroundTruthClaim, TestsetEntry, iter_claims, load_testset

__all__ = [
    "JUDGE_MODEL",
    "DEFAULT_TESTSET_DIR",
    "MAX_FACTUALITY_SAMPLES",
    "RepoScorecard",
    "RunResult",
    "TestsetEntry",
    "GroundTruthClaim",
    "load_testset",
    "iter_claims",
    "score_repo",
    "aggregate",
    "write_scoreboard",
    "judge_factuality_pair",
    "judge_coverage_pair",
    "judge_completeness",
    "judge_readability",
    "citation_density",
]
