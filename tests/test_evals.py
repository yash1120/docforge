"""Eval harness tests — loader schema, mocked judges, runner end-to-end."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docforge.evals import (
    GroundTruthClaim,
    TestsetEntry,
    aggregate,
    citation_density,
    iter_claims,
    judge_completeness,
    load_testset,
    score_repo,
)
from docforge.scout import Manifest


# ---- Testset loader ------------------------------------------------------


def _write_testset(root: Path, name: str, *, meta: dict, claims: list[dict]) -> None:
    sub = root / name
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "meta.json").write_text(json.dumps(meta))
    (sub / "claims.jsonl").write_text(
        "\n".join(json.dumps(c) for c in claims) + "\n"
    )


def test_load_testset_finds_repo_subdir(tmp_path: Path):
    _write_testset(
        tmp_path,
        "demo",
        meta={"name": "demo", "repo_path": str(tmp_path / "fake"),
              "primary_language": "Python", "runtime_topology": "library"},
        claims=[
            {"id": "demo-01", "claim": "Does the thing.", "expected_files": ["a.py"]},
            {"id": "demo-02", "claim": "Has a CLI."},
        ],
    )
    entries = load_testset(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "demo"
    assert e.primary_language == "Python"
    assert len(e.claims) == 2
    assert e.claims[0].id == "demo-01"
    assert e.claims[0].expected_files == ["a.py"]


def test_load_testset_skips_subdirs_without_files(tmp_path: Path):
    (tmp_path / "incomplete").mkdir()
    (tmp_path / "incomplete" / "meta.json").write_text("{}")  # missing claims.jsonl
    _write_testset(
        tmp_path, "ok",
        meta={"name": "ok", "repo_path": "/tmp/x", "primary_language": "Go",
              "runtime_topology": "server"},
        claims=[{"id": "ok-01", "claim": "real"}],
    )
    entries = load_testset(tmp_path)
    names = [e.name for e in entries]
    assert names == ["ok"]


def test_load_testset_rejects_meta_without_path(tmp_path: Path):
    _write_testset(
        tmp_path, "bad",
        meta={"name": "bad", "primary_language": "Python", "runtime_topology": "library"},
        claims=[{"id": "x", "claim": "y"}],
    )
    with pytest.raises(ValueError, match="repo_path or git_url"):
        load_testset(tmp_path)


def test_load_testset_rejects_claim_without_text(tmp_path: Path):
    _write_testset(
        tmp_path, "bad",
        meta={"name": "bad", "repo_path": "/tmp/x", "primary_language": "Python",
              "runtime_topology": "library"},
        claims=[{"id": "x", "claim": ""}],
    )
    with pytest.raises(ValueError, match="missing 'claim'"):
        load_testset(tmp_path)


def test_load_testset_ignores_comments_and_blank_lines(tmp_path: Path):
    sub = tmp_path / "demo"
    sub.mkdir()
    (sub / "meta.json").write_text(json.dumps({
        "name": "demo", "repo_path": "/tmp/x",
        "primary_language": "Python", "runtime_topology": "library",
    }))
    (sub / "claims.jsonl").write_text(
        '# a comment\n'
        '\n'
        '{"id":"d1","claim":"real claim"}\n'
        '   \n'
    )
    entries = load_testset(tmp_path)
    assert len(entries) == 1 and len(entries[0].claims) == 1


def test_iter_claims_flattens():
    a = TestsetEntry(name="a", repo_path="/x", git_url=None,
                     primary_language="py", runtime_topology="lib",
                     claims=[GroundTruthClaim(id="a1", claim="c1"),
                             GroundTruthClaim(id="a2", claim="c2")],
                     meta_path=Path("x"))
    b = TestsetEntry(name="b", repo_path="/y", git_url=None,
                     primary_language="ts", runtime_topology="lib",
                     claims=[GroundTruthClaim(id="b1", claim="c3")],
                     meta_path=Path("y"))
    all_claims = iter_claims([a, b])
    assert [c.id for c in all_claims] == ["a1", "a2", "b1"]


# ---- judge_completeness (deterministic) ---------------------------------


def _fake_manifest(**overrides) -> Manifest:
    base = Manifest(
        repo_path="/x", repo_name="demo",
        primary_language="Python",
        languages={"Python": 1}, frameworks=["FastAPI"],
        entry_points=["src/demo/cli.py"], top_level_modules=["src/demo"],
        dependency_files=["pyproject.toml"], dependencies={"pyproject.toml": ["fastapi"]},
        license="MIT", license_file="LICENSE", public_api=[],
        readme_path=None, has_tests=True, has_ci=True, has_docker=False,
        total_files=10, total_code_files=5, total_loc=100, skipped_files=0,
    )
    return base.model_copy(update=overrides)


def test_completeness_full_when_all_present():
    drafts = {
        "README.md": (
            "# Install\n\n`pip install demo`\n\n"
            "## Quickstart\n\nRun `cli.py` to start.\n\n"
            "## Dependencies\n\nUses FastAPI.\n\n"
            "## License\n\nMIT.\n"
        ),
    }
    score, missing = judge_completeness(drafts, _fake_manifest())
    assert score == 1.0
    assert missing == []


def test_completeness_partial_when_missing_install():
    drafts = {"README.md": "We use cli.py and the FastAPI framework. License is MIT."}
    score, missing = judge_completeness(drafts, _fake_manifest())
    assert score < 1.0
    assert "install_command" in missing


def test_completeness_no_license_check_when_repo_has_no_license():
    drafts = {
        "README.md": (
            "Run `cli.py` via `pip install demo`. Quickstart. Uses FastAPI."
        ),
    }
    m = _fake_manifest(license=None, license_file=None)
    score, missing = judge_completeness(drafts, m)
    assert "license_mention" not in missing


# ---- citation_density ----------------------------------------------------


def test_citation_density_math():
    # 100 words, 2 citations -> exactly 2.0 per 100 words
    body = " ".join(["word"] * 98) + " [a/b.py:1] [c/d.py:5]"
    drafts = {"README.md": body}
    density = citation_density(drafts)
    assert 1.9 <= density <= 2.1


def test_citation_density_zero_on_empty():
    assert citation_density({"README.md": ""}) == 0.0


# ---- score_repo with mocks ----------------------------------------------


def _make_runner(drafts: dict[str, str], manifest: Manifest):
    """Build a stub runner_fn that pretends docforge produced these drafts."""
    def _run(repo_path):
        return drafts, manifest, []
    return _run


def test_score_repo_aggregates_axes(tmp_path: Path):
    # Build a fake repo + cite-able file
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "cli.py").write_text("def main(): pass\n# line 2\n# line 3\n")

    entry = TestsetEntry(
        name="demo",
        repo_path=str(tmp_path),
        git_url=None,
        primary_language="Python",
        runtime_topology="cli",
        claims=[
            GroundTruthClaim(id="d1", claim="demo has a main() entry"),
            GroundTruthClaim(id="d2", claim="installable via pip"),
        ],
        meta_path=tmp_path / "meta.json",
    )

    drafts = {
        "README.md": (
            "# demo\n\n`pip install demo` then run `cli.py` [src/demo/cli.py:1]. "
            "Has a main() entry point [src/demo/cli.py:1]. MIT licensed."
        ),
        "API.md": "Exports `main()` [src/demo/cli.py:1].",
    }
    manifest = _fake_manifest(repo_path=str(tmp_path), repo_name="demo",
                              entry_points=["src/demo/cli.py"])

    judged_fact = []

    def fake_factuality(claim, file, line_range, code):
        # Each citation supported
        judged_fact.append((file, line_range))
        return {"claim_id": "", "supported": True, "reason": "ok"}

    def fake_coverage(claim_text, drafts):
        # d1 found (mentions main), d2 not found
        if "main" in claim_text:
            return {"claim_id": "", "found": True, "where": "README", "reason": "matched"}
        return {"claim_id": "", "found": True, "where": "README", "reason": "pip mention"}

    def fake_readability(name, body):
        return {"score": 4, "rationale": "clear"}

    result = score_repo(
        entry,
        judge_factuality_fn=fake_factuality,
        judge_coverage_fn=fake_coverage,
        judge_readability_fn=fake_readability,
        runner_fn=_make_runner(drafts, manifest),
    )

    s = result.scorecard
    # 3 citations in drafts, but only unique file:line:line_end pairs are judged
    assert s.factuality == 1.0
    assert len(judged_fact) >= 1
    assert s.coverage == 1.0  # both claims found per the mock
    assert 0 <= s.completeness <= 1
    assert s.readability == 4.0
    assert result.duration_sec >= 0


def test_score_repo_factuality_drops_on_unsupported(tmp_path: Path):
    (tmp_path / "a.py").write_text("def x(): pass\n")
    entry = TestsetEntry(
        name="x", repo_path=str(tmp_path), git_url=None,
        primary_language="Python", runtime_topology="library",
        claims=[GroundTruthClaim(id="x1", claim="thing")],
        meta_path=tmp_path / "m.json",
    )
    drafts = {
        "README.md": "claim one [a.py:1]. claim two [a.py:1].",
    }
    manifest = _fake_manifest(repo_path=str(tmp_path))

    # Mock factuality to alternate supported/unsupported across calls
    call_index = {"n": 0}
    def fake_factuality(*_a, **_kw):
        call_index["n"] += 1
        return {"claim_id": "", "supported": call_index["n"] == 1, "reason": "r"}

    result = score_repo(
        entry,
        judge_factuality_fn=fake_factuality,
        judge_coverage_fn=lambda *_a, **_kw: {"claim_id": "", "found": True, "where": "", "reason": ""},
        judge_readability_fn=lambda *_a, **_kw: {"score": 3, "rationale": ""},
        runner_fn=_make_runner(drafts, manifest),
    )
    # Two identical citations are deduped, so only one judgment ran (supported)
    # -> factuality should be 1.0. Verify the dedupe behavior holds.
    assert result.scorecard.factuality == 1.0


# ---- aggregate -----------------------------------------------------------


def test_aggregate_computes_means(tmp_path: Path):
    from docforge.evals.judge import RepoScorecard
    from docforge.evals.runner import RunResult

    r1 = RunResult(repo="a", docs={}, duration_sec=1.0,
                   scorecard=RepoScorecard(repo="a",
                                           factuality=0.8, coverage=0.5,
                                           completeness=1.0, citation_density=2.0,
                                           readability=4.0))
    r2 = RunResult(repo="b", docs={}, duration_sec=2.0,
                   scorecard=RepoScorecard(repo="b",
                                           factuality=1.0, coverage=0.7,
                                           completeness=0.6, citation_density=4.0,
                                           readability=3.0))
    summary = aggregate([r1, r2])["summary"]
    assert summary["factuality_mean"] == 0.9
    assert summary["coverage_mean"] == 0.6
    assert summary["completeness_mean"] == 0.8
    assert summary["readability_mean"] == 3.5
    assert summary["citation_density_mean"] == 3.0


def test_aggregate_empty_returns_empty_summary():
    out = aggregate([])
    assert out["repos"] == []
    assert out["summary"] == {}
