"""LangGraph supervisor — wires the agent team into a pipeline with a critic loop.

Shape (Week 4):
    START
      |
      +-> test_scout ----\
      +-> api_scanner ----+--> reader -> architect -> diagrammer -> writer -> critic
      +-> config_reader -/                                                      |
                                                                                v
                                            (issues>0 and cycles<MAX ? editor -> critic : END)

The three "scout" agents run in parallel at the start — they're pure static
analysis, no LLM, so they're cheap and embarrassingly parallel. They write to
disjoint keys in GraphState (test_summary, api_routes, config_summary) so
LangGraph's merge has no conflicts.

Pass `critic_loop=False` to compile the linear-only graph (used for the
single-pass baseline comparison in the eval).
"""

from __future__ import annotations

import os
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from ..indexer import CodeIndex
from .api_scanner import run_api_scanner
from .architect import run_architect
from .config_reader import run_config_reader
from .critic import run_critic
from .diagrammer import run_diagrammer
from .editor import run_editor
from .reader import run_reader
from .state import GraphState
from .test_scout import run_test_scout
from .writer import run_writer


RetrieveFn = Callable[[str, int, dict | None], list[dict]]

# Maximum number of critic <-> editor revisions. The plan locks this at 2.
MAX_CRITIC_CYCLES = 2


def make_retrieve(index: CodeIndex) -> RetrieveFn:
    """Closure binder so the graph nodes don't need to know about CodeIndex."""
    def _retrieve(query: str, k: int, where: dict | None) -> list[dict]:
        return index.query(query, k=k, where=where)
    return _retrieve


def build_graph(index: CodeIndex, *, critic_loop: bool = True) -> Any:
    """Construct and compile the LangGraph StateGraph.

    Set `critic_loop=False` for the linear-only Week-2 graph (no critic/editor),
    which is the apples-to-apples comparison point for the eval.
    """
    retrieve = make_retrieve(index)

    def reader_node(state: GraphState) -> dict:
        return run_reader(state, retrieve=retrieve)

    def critic_node(state: GraphState) -> dict:
        return run_critic(state, retrieve=retrieve)

    g = StateGraph(GraphState)
    # Parallel scout branch (no LLM, fan-out from START)
    g.add_node("test_scout", run_test_scout)
    g.add_node("api_scanner", run_api_scanner)
    g.add_node("config_reader", run_config_reader)
    # LLM agents
    g.add_node("reader", reader_node)
    g.add_node("architect", run_architect)
    g.add_node("diagrammer", run_diagrammer)
    g.add_node("writer", run_writer)

    # Fan-out: all three scouts start in parallel from START.
    g.add_edge(START, "test_scout")
    g.add_edge(START, "api_scanner")
    g.add_edge(START, "config_reader")

    # Fan-in: reader waits for all three before running.
    g.add_edge("test_scout", "reader")
    g.add_edge("api_scanner", "reader")
    g.add_edge("config_reader", "reader")

    g.add_edge("reader", "architect")
    g.add_edge("architect", "diagrammer")
    g.add_edge("diagrammer", "writer")

    if critic_loop:
        g.add_node("critic", critic_node)
        g.add_node("editor", run_editor)

        g.add_edge("writer", "critic")
        g.add_conditional_edges(
            "critic",
            _route_after_critic,
            {"editor": "editor", "end": END},
        )
        g.add_edge("editor", "critic")
    else:
        g.add_edge("writer", END)

    return g.compile()


def _route_after_critic(state: GraphState) -> str:
    """Loop back to editor if there are issues and we haven't hit the cycle cap."""
    critique = state.get("critique") or {}
    issues = critique.get("issues") or []
    cycles = int(state.get("cycles", 0))
    if issues and cycles < MAX_CRITIC_CYCLES:
        return "editor"
    return "end"


def langfuse_callbacks() -> list[Any]:
    """Return a Langfuse callback list if keys are set, else []."""
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return []
    try:
        from langfuse.langchain import CallbackHandler  # type: ignore[import]
        return [CallbackHandler()]
    except Exception:
        return []
