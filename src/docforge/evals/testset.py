"""Testset loader + schema.

Each entry in `eval/testset/<repo>/` is:
  - `meta.json`: { name, repo_path (local) OR git_url, primary_language, runtime_topology }
  - `claims.jsonl`: one ground-truth claim per line:
        { "id", "claim", "expected_files": [...], "module": "..." (optional) }

Hand-labelling is the bottleneck — the plan locks 5 repos x 20 claims = 100 total.
The loader validates the shape so the runner can't quietly load garbage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class GroundTruthClaim:
    id: str
    claim: str
    expected_files: list[str] = field(default_factory=list)
    module: str | None = None


@dataclass
class TestsetEntry:
    # __test__ = False stops pytest from trying to collect this dataclass as a test class.
    __test__ = False

    name: str
    repo_path: str | None         # local absolute path; one of repo_path/git_url required
    git_url: str | None
    primary_language: str
    runtime_topology: str
    claims: list[GroundTruthClaim]
    meta_path: Path               # source location for error messages

    @property
    def source(self) -> str:
        return self.repo_path or self.git_url or "?"


def load_testset(root: Path | str) -> list[TestsetEntry]:
    """Load every <repo>/ subdir under `root` that has a meta.json + claims.jsonl."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"testset root not found: {root}")

    entries: list[TestsetEntry] = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        meta_path = sub / "meta.json"
        claims_path = sub / "claims.jsonl"
        if not meta_path.exists() or not claims_path.exists():
            continue
        entries.append(_load_one(meta_path, claims_path))
    return entries


def _load_one(meta_path: Path, claims_path: Path) -> TestsetEntry:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    name = str(meta.get("name") or meta_path.parent.name)
    repo_path = meta.get("repo_path")
    git_url = meta.get("git_url")
    if not repo_path and not git_url:
        raise ValueError(f"{meta_path}: must specify repo_path or git_url")

    claims: list[GroundTruthClaim] = []
    for i, line in enumerate(claims_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{claims_path}:{i}: invalid JSON ({e})") from None
        cid = str(obj.get("id") or f"{name}-{i}")
        text = str(obj.get("claim") or "").strip()
        if not text:
            raise ValueError(f"{claims_path}:{i}: missing 'claim' field")
        expected = [str(x) for x in (obj.get("expected_files") or [])]
        module = obj.get("module")
        claims.append(GroundTruthClaim(id=cid, claim=text, expected_files=expected, module=module))

    return TestsetEntry(
        name=name,
        repo_path=str(repo_path) if repo_path else None,
        git_url=str(git_url) if git_url else None,
        primary_language=str(meta.get("primary_language") or "unknown"),
        runtime_topology=str(meta.get("runtime_topology") or "library"),
        claims=claims,
        meta_path=meta_path,
    )


def iter_claims(entries: Iterable[TestsetEntry]) -> list[GroundTruthClaim]:
    return [c for e in entries for c in e.claims]
