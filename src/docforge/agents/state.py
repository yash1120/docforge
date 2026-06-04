"""Single source of truth for the LangGraph state.

Every agent reads from and writes to a `GraphState` TypedDict. Non-JSON-serializable
things (CodeIndex, LLM clients) are passed via closures in `supervisor.build_graph()`
so the state stays cleanly serializable for checkpointing/tracing.
"""

from __future__ import annotations

from typing import Any, TypedDict

from ..scout import Manifest


class ModuleSummary(TypedDict):
    """One Reader output per top-level module."""

    module: str
    purpose: str
    public_api: list[str]      # symbol or short signature
    key_behaviors: list[str]   # one-liners
    citations: list[str]       # "path/to/file.py:42-58"


class Component(TypedDict):
    name: str
    purpose: str
    files: list[str]
    citations: list[str]


class Edge(TypedDict):
    src: str        # component name
    dst: str
    via: str        # one-line label


class ExternalDep(TypedDict):
    name: str
    role: str


class Architecture(TypedDict):
    components: list[Component]
    edges: list[Edge]
    external_deps: list[ExternalDep]
    runtime_topology: str   # "cli" / "server" / "worker" / "library" / "hybrid"


class TestSummary(TypedDict):
    total_test_files: int
    total_test_cases: int
    frameworks: list[str]            # ["pytest", "jest", ...]
    tested_symbols: list[str]        # public_api entries found in test files
    untested_symbols: list[str]      # public_api entries NOT found in test files
    citations: list[str]             # representative "tests/foo.py:42" hits


class APIRoute(TypedDict):
    kind: str                        # "http" | "cli"
    method: str                      # "GET" | "POST" | "CLI" | etc.
    path: str                        # "/users/{id}" or "docforge eval"
    handler: str                     # function name
    citation: str                    # "src/api.py:42"


class EnvVar(TypedDict):
    name: str
    default: str | None
    required: bool
    citation: str


class ConfigSummary(TypedDict):
    env_vars: list[EnvVar]
    config_files: list[str]          # repo-relative paths to settings/config files


class GraphState(TypedDict, total=False):
    """Shared state. `total=False` so we can fill it in stages."""

    # Inputs (CLI populates these before invoking the graph)
    repo_path: str
    repo_name: str
    manifest: Manifest
    out_dir: str

    # Specialist scouts (parallel branch from START — all static, no LLM)
    test_summary: TestSummary
    api_routes: list[APIRoute]
    config_summary: ConfigSummary

    # Reader output
    module_summaries: list[ModuleSummary]

    # Architect output
    architecture: Architecture

    # Diagrammer output
    diagram_mmd: str
    diagram_attempts: int

    # Writer output
    drafts: dict[str, str]   # "README.md" -> markdown

    # Critic loop (Week 3 — kept here so the state shape is final-ish)
    critique: dict[str, Any]
    cycles: int

    # Bookkeeping
    errors: list[str]


def initial_state(repo_path: str, repo_name: str, manifest: Manifest, out_dir: str) -> GraphState:
    return GraphState(
        repo_path=repo_path,
        repo_name=repo_name,
        manifest=manifest,
        out_dir=out_dir,
        test_summary=TestSummary(
            total_test_files=0, total_test_cases=0,
            frameworks=[], tested_symbols=[], untested_symbols=[], citations=[],
        ),
        api_routes=[],
        config_summary=ConfigSummary(env_vars=[], config_files=[]),
        module_summaries=[],
        architecture=Architecture(
            components=[], edges=[], external_deps=[], runtime_topology=""
        ),
        diagram_mmd="",
        diagram_attempts=0,
        drafts={},
        critique={},
        cycles=0,
        errors=[],
    )
