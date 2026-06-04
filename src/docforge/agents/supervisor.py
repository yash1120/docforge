"""LangGraph supervisor — wires the agent team into a pipeline with a critic loop.

Week 2 shape:  reader -> architect -> diagrammer -> writer -> END
Week 3 shape:  reader -> architect -> diagrammer -> writer -> critic
                -> (issues>0 and cycles<MAX ? editor -> critic : END)

Pass `critic_loop=False` to compile the Week-2 linear graph (used for the
single-pass baseline comparison in the eval).
"""

from __future__ import annotations

import os
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from ..indexer import CodeIndex
from .architect import run_architect
from .critic import run_critic
from .diagrammer import run_diagrammer
from .editor import run_editor
from .reader import run_reader
from .state import GraphState
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
    g.add_node("reader", reader_node)
    g.add_node("architect", run_architect)
    g.add_node("diagrammer", run_diagrammer)
    g.add_node("writer", run_writer)

    g.add_edge(START, "reader")
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
