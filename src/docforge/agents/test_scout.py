"""TestScout — static analysis of test files.

Runs in parallel with APIScanner and ConfigReader from the supervisor's START
node. No LLM. Purely regex + path heuristics.

What it produces:
  * test framework detection (pytest, unittest, jest/vitest, go test, cargo test)
  * test-case count
  * which `manifest.public_api` symbols are referenced by tests vs not
  * representative citations so downstream agents can ground claims about coverage

The motivation: an honest README should say "23 tests across 5 modules; the
`auth/` module has zero test coverage" — not "the codebase is well-tested."
"""

from __future__ import annotations

import re
from pathlib import Path

from ..scout.walk import walk_repo
from .state import GraphState, TestSummary


_PY_TEST_FN = re.compile(r"^\s*(async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)
_PY_TEST_CLASS = re.compile(r"^\s*class\s+(Test[A-Za-z0-9_]+)\b", re.MULTILINE)
_JS_TEST_CASE = re.compile(r"\b(test|it|describe)\s*\(\s*['\"]([^'\"]+)['\"]")
_GO_TEST_FN = re.compile(r"^\s*func\s+(Test[A-Za-z0-9_]+)\s*\(", re.MULTILINE)
_RUST_TEST = re.compile(r"#\[test\]")

_TEST_PATH_RE = re.compile(
    r"(?:^|/)(tests?|__tests__|spec)(?:/|$)|(?:_test|\.test|\.spec)\.(?:py|js|ts|tsx|jsx|go|rs)$",
    re.IGNORECASE,
)


def _is_test_file(rel: str) -> bool:
    return bool(_TEST_PATH_RE.search(rel))


def _count_python_tests(text: str) -> int:
    return len(_PY_TEST_FN.findall(text)) + len(_PY_TEST_CLASS.findall(text))


def _count_js_tests(text: str) -> int:
    # Only `test(` and `it(` count as test cases — `describe` is a grouping.
    return sum(1 for m in _JS_TEST_CASE.finditer(text) if m.group(1) in ("test", "it"))


def _count_go_tests(text: str) -> int:
    return len(_GO_TEST_FN.findall(text))


def _count_rust_tests(text: str) -> int:
    return len(_RUST_TEST.findall(text))


def _framework_for(rel: str, text: str) -> str | None:
    if rel.endswith(".py"):
        if "import pytest" in text or "from pytest" in text or _PY_TEST_FN.search(text):
            return "pytest"
        if "import unittest" in text or "TestCase" in text:
            return "unittest"
        return "pytest" if _PY_TEST_FN.search(text) else None
    if rel.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")):
        if "from 'vitest'" in text or "from \"vitest\"" in text:
            return "vitest"
        if "from '@jest" in text or "jest" in text:
            return "jest"
        return "jest/vitest" if _JS_TEST_CASE.search(text) else None
    if rel.endswith(".go"):
        return "go test" if _GO_TEST_FN.search(text) else None
    if rel.endswith(".rs"):
        return "cargo test" if _RUST_TEST.search(text) else None
    return None


_BARE_SYMBOL = re.compile(r"[:.]([A-Za-z_][A-Za-z0-9_]*)$")


def _bare_symbols(public_api: list[str]) -> list[str]:
    out: list[str] = []
    for entry in public_api:
        m = _BARE_SYMBOL.search(entry)
        if m:
            out.append(m.group(1))
    return out


def run_test_scout(state: GraphState) -> dict:
    manifest = state["manifest"]
    repo_root = Path(manifest.repo_path)

    files, _ = walk_repo(repo_root)
    test_files: list[Path] = []
    for p in files:
        try:
            rel = str(p.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            continue
        if _is_test_file(rel):
            test_files.append(p)

    total_cases = 0
    frameworks: set[str] = set()
    citations: list[str] = []
    test_blob_lines: list[str] = []

    for p in test_files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(repo_root)).replace("\\", "/")

        fw = _framework_for(rel, text)
        if fw:
            frameworks.add(fw)

        cases = 0
        if rel.endswith(".py"):
            cases = _count_python_tests(text)
        elif rel.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")):
            cases = _count_js_tests(text)
        elif rel.endswith(".go"):
            cases = _count_go_tests(text)
        elif rel.endswith(".rs"):
            cases = _count_rust_tests(text)
        total_cases += cases

        if cases and len(citations) < 5:
            # Cite the line of the first test in this file
            m = _PY_TEST_FN.search(text) or _JS_TEST_CASE.search(text) or _GO_TEST_FN.search(text) or _RUST_TEST.search(text)
            if m:
                line_no = text[: m.start()].count("\n") + 1
                citations.append(f"{rel}:{line_no}")

        test_blob_lines.append(text)

    blob = "\n".join(test_blob_lines)

    tested: list[str] = []
    untested: list[str] = []
    for sym in _bare_symbols(manifest.public_api):
        if re.search(rf"\b{re.escape(sym)}\b", blob):
            tested.append(sym)
        else:
            untested.append(sym)

    summary = TestSummary(
        total_test_files=len(test_files),
        total_test_cases=total_cases,
        frameworks=sorted(frameworks),
        tested_symbols=tested[:60],
        untested_symbols=untested[:60],
        citations=citations,
    )
    return {"test_summary": summary}
