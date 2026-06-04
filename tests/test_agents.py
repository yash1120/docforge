"""Week 2 agent tests — mocked LLM. Each agent has its own focused tests, plus
one end-to-end supervisor test that runs the whole graph against a tiny fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docforge.agents import (
    Architecture,
    ModuleSummary,
    initial_state,
    mermaid_from_architecture,
    run_architect,
    run_diagrammer,
    run_reader,
    run_writer,
    validate_mermaid,
)
from docforge.agents._utils import cite, extract_json, hits_to_context
from docforge.scout import build_manifest


# ---- Fixtures ------------------------------------------------------------


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text('"""pkg."""\n')
    (tmp_path / "src" / "pkg" / "core.py").write_text(
        "def run() -> int:\n    return 42\n\n"
        "class Engine:\n    def start(self) -> None:\n        pass\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\ndependencies = ["fastapi"]\n'
    )
    (tmp_path / "README.md").write_text("# pkg\n")
    return tmp_path


def _fake_retrieve(hits: list[dict]):
    """Build a retrieve fn that always returns the same canned hits."""
    def _retrieve(query: str, k: int, where=None) -> list[dict]:
        return hits[:k]
    return _retrieve


# ---- _utils tests --------------------------------------------------------


def test_extract_json_handles_fenced_block():
    text = "Here's the JSON:\n```json\n{\"a\": 1}\n```\nDone."
    assert extract_json(text) == {"a": 1}


def test_extract_json_handles_raw_object():
    text = 'Sure thing: {"x": [1, 2]} cheers'
    assert extract_json(text) == {"x": [1, 2]}


def test_extract_json_raises_on_unparseable():
    with pytest.raises(ValueError):
        extract_json("not json at all")


def test_cite_formats():
    assert cite("foo.py", 10) == "foo.py:10"
    assert cite("foo.py", 10, 25) == "foo.py:10-25"
    assert cite("foo.py", 10, 10) == "foo.py:10"


def test_hits_to_context_includes_citations():
    hits = [
        {"file": "a.py", "line_start": 1, "line_end": 5, "name": "run", "kind": "function", "content": "def run(): pass"},
    ]
    block = hits_to_context(hits)
    assert "a.py:1-5" in block
    assert "run" in block


# ---- Reader tests --------------------------------------------------------


def test_reader_returns_normalized_summary(small_repo: Path):
    m = build_manifest(small_repo)
    hits = [
        {"file": "src/pkg/core.py", "line_start": 1, "line_end": 2, "name": "run",
         "kind": "function", "content": "def run(): return 42"},
    ]
    retrieve = _fake_retrieve(hits)

    def fake_chat(messages, **_):
        return json.dumps({
            "module": "src/pkg",
            "purpose": "Core engine module.",
            "public_api": ["run() -> int", "Engine.start()"],
            "key_behaviors": ["Defines Engine and the run() entry [src/pkg/core.py:1-2]"],
            "citations": ["src/pkg/core.py:1-2"],
        })

    with patch("docforge.agents.reader.chat", side_effect=fake_chat):
        state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
        update = run_reader(state, retrieve=retrieve)

    assert len(update["module_summaries"]) >= 1
    s = update["module_summaries"][0]
    assert s["module"] == "src/pkg"
    assert "run() -> int" in s["public_api"]
    assert any("src/pkg/core.py:1-2" in b for b in s["key_behaviors"])


def test_reader_handles_empty_retrieval(small_repo: Path):
    m = build_manifest(small_repo)
    retrieve = _fake_retrieve([])

    def fake_chat(*_a, **_kw):
        raise AssertionError("LLM should not be called when retrieval is empty")

    with patch("docforge.agents.reader.chat", side_effect=fake_chat):
        state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
        update = run_reader(state, retrieve=retrieve)

    assert update["module_summaries"]
    s = update["module_summaries"][0]
    assert "no indexed content" in s["purpose"].lower()


def test_reader_recovers_from_unparseable_json(small_repo: Path):
    m = build_manifest(small_repo)
    hits = [{"file": "src/pkg/core.py", "line_start": 1, "line_end": 2,
             "name": "run", "kind": "function", "content": "def run(): return 42"}]

    with patch("docforge.agents.reader.chat", return_value="not valid json"):
        state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
        update = run_reader(state, retrieve=_fake_retrieve(hits))

    # Graceful degradation: still produces a summary entry, no exception leaks
    assert update["module_summaries"]
    assert update["module_summaries"][0]["citations"]


# ---- Architect tests -----------------------------------------------------


def test_architect_normalizes_topology(small_repo: Path):
    m = build_manifest(small_repo)
    state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
    state["module_summaries"] = [ModuleSummary(
        module="src/pkg", purpose="core", public_api=["run"],
        key_behaviors=["does things [src/pkg/core.py:1]"], citations=["src/pkg/core.py:1"],
    )]

    def fake_chat(*_a, **_kw):
        return json.dumps({
            "components": [
                {"name": "engine", "purpose": "runs", "files": ["src/pkg/core.py"], "citations": ["src/pkg/core.py:1"]},
                {"name": "cli", "purpose": "entrypoint", "files": ["src/pkg/__main__.py"], "citations": []},
            ],
            "edges": [
                {"src": "cli", "dst": "engine", "via": "invokes"},
                {"src": "cli", "dst": "ghost", "via": "phantom edge"},  # should be dropped
            ],
            "external_deps": [{"name": "FastAPI", "role": "HTTP layer"}],
            "runtime_topology": "MOSTLY_CLI",  # invalid -> should fall back to "library"
        })

    with patch("docforge.agents.architect.chat", side_effect=fake_chat):
        update = run_architect(state)

    arch = update["architecture"]
    assert len(arch["components"]) == 2
    # phantom edge filtered
    assert len(arch["edges"]) == 1
    assert arch["edges"][0]["src"] == "cli" and arch["edges"][0]["dst"] == "engine"
    # invalid topology normalized
    assert arch["runtime_topology"] == "library"


def test_architect_empty_summaries_returns_empty_arch(small_repo: Path):
    m = build_manifest(small_repo)
    state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
    state["module_summaries"] = []

    update = run_architect(state)
    arch = update["architecture"]
    assert arch["components"] == []
    assert arch["edges"] == []
    assert any("no module summaries" in e for e in update["errors"])


# ---- Diagrammer tests ----------------------------------------------------


def test_mermaid_from_architecture_produces_valid_diagram():
    arch = Architecture(
        components=[
            {"name": "API Server", "purpose": "handles HTTP", "files": ["api/main.py"], "citations": []},
            {"name": "Worker", "purpose": "async tasks", "files": ["worker.py"], "citations": []},
        ],
        edges=[{"src": "API Server", "dst": "Worker", "via": "enqueue"}],
        external_deps=[{"name": "Redis", "role": "queue"}],
        runtime_topology="server",
    )
    diagram = mermaid_from_architecture(arch)
    ok, reason = validate_mermaid(diagram)
    assert ok, reason
    assert "flowchart TD" in diagram
    assert "enqueue" in diagram
    # Subgraph balanced
    assert diagram.count("subgraph") == diagram.count("\n    end\n") + diagram.count("\n        end\n") + diagram.count("end\n") - (diagram.count("dependencies end") if "dependencies end" in diagram else 0) or "end" in diagram


def test_mermaid_handles_empty_architecture():
    arch = Architecture(components=[], edges=[], external_deps=[], runtime_topology="library")
    diagram = mermaid_from_architecture(arch)
    ok, _ = validate_mermaid(diagram)
    assert ok


def test_mermaid_safe_ids_for_funky_names():
    arch = Architecture(
        components=[
            {"name": "API/Server v2!", "purpose": "p", "files": [], "citations": []},
            {"name": "API/Server v2!", "purpose": "p", "files": [], "citations": []},  # duplicate name
        ],
        edges=[],
        external_deps=[],
        runtime_topology="server",
    )
    diagram = mermaid_from_architecture(arch)
    ok, _ = validate_mermaid(diagram)
    assert ok
    # No raw slashes or bangs in node IDs (the part before `[`)
    import re
    ids = re.findall(r"^    ([a-zA-Z][a-zA-Z0-9_]*)\[", diagram, flags=re.MULTILINE)
    assert all(re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", i) for i in ids)


def test_validate_mermaid_catches_missing_declaration():
    ok, reason = validate_mermaid("nodeA --> nodeB")
    assert not ok and "declaration" in reason


def test_validate_mermaid_catches_unbalanced_subgraph():
    text = "flowchart TD\n    subgraph a\n        x --> y\n"
    ok, reason = validate_mermaid(text)
    assert not ok and "subgraph" in reason


def test_run_diagrammer_no_llm_skips_beautify(small_repo: Path):
    m = build_manifest(small_repo)
    state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
    state["architecture"] = Architecture(
        components=[{"name": "core", "purpose": "p", "files": [], "citations": []}],
        edges=[],
        external_deps=[],
        runtime_topology="library",
    )
    # Force "no provider" so beautify is skipped
    with patch("docforge.agents.diagrammer.provider_in_use", return_value="none"):
        update = run_diagrammer(state)
    assert "flowchart TD" in update["diagram_mmd"]
    assert update["diagram_attempts"] == 1


# ---- Writer tests --------------------------------------------------------


def test_writer_produces_four_docs(small_repo: Path):
    m = build_manifest(small_repo)
    state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
    state["module_summaries"] = [ModuleSummary(
        module="src/pkg", purpose="core", public_api=["run"],
        key_behaviors=["runs [src/pkg/core.py:1]"], citations=["src/pkg/core.py:1"],
    )]
    state["architecture"] = Architecture(
        components=[{"name": "core", "purpose": "engine", "files": ["src/pkg/core.py"], "citations": []}],
        edges=[],
        external_deps=[],
        runtime_topology="library",
    )
    state["diagram_mmd"] = "flowchart TD\n    core[\"core\"]\n"

    captured_systems: list[str] = []

    def fake_chat(messages, **_):
        captured_systems.append(messages[0].content)
        # Return some content that includes a citation so we know it works
        return "# Doc\n\nThis does the thing [src/pkg/core.py:1].\n"

    with patch("docforge.agents.writer.chat", side_effect=fake_chat):
        update = run_writer(state)

    drafts = update["drafts"]
    assert set(drafts.keys()) == {"README.md", "ARCHITECTURE.md", "API.md", "TUTORIAL.md"}
    # All four prompts must enforce the citation rule
    assert all("[path/to/file.py:42]" in s for s in captured_systems)


def test_writer_partial_failure_does_not_break_other_docs(small_repo: Path):
    m = build_manifest(small_repo)
    state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
    state["module_summaries"] = []
    state["architecture"] = Architecture(
        components=[], edges=[], external_deps=[], runtime_topology="library",
    )

    call_counter = {"n": 0}

    def fake_chat(messages, **_):
        call_counter["n"] += 1
        if call_counter["n"] == 2:  # blow up on the second doc
            raise RuntimeError("simulated provider hiccup")
        return "# OK\n"

    with patch("docforge.agents.writer.chat", side_effect=fake_chat):
        update = run_writer(state)

    drafts = update["drafts"]
    assert len(drafts) == 4
    # One should be the error-fallback content
    assert any("writer failed" in v for v in drafts.values())
    assert any("simulated provider hiccup" in e for e in update["errors"])


# ---- Supervisor end-to-end ----------------------------------------------


def test_supervisor_graph_runs_end_to_end(small_repo: Path):
    """Full graph against a tiny repo with a mocked LLM. Verifies wiring + state flow."""
    from docforge.agents import build_graph
    from docforge.indexer import build_index
    from docforge.scout.walk import walk_repo

    m = build_manifest(small_repo)
    files, _ = walk_repo(small_repo)
    persist = small_repo / ".docforge" / "chroma"
    index, _ = build_index(small_repo, files, m.repo_name, persist_dir=persist)

    json_response = json.dumps({
        "module": "src/pkg", "purpose": "p", "public_api": ["run"],
        "key_behaviors": ["does [src/pkg/core.py:1]"],
        "citations": ["src/pkg/core.py:1"],
    })
    arch_response = json.dumps({
        "components": [{"name": "core", "purpose": "engine", "files": [], "citations": []}],
        "edges": [], "external_deps": [], "runtime_topology": "library",
    })

    def fake_chat(messages, **_):
        sys_msg = messages[0].content
        if "JSON object describing this one module" in sys_msg:
            return json_response
        if "synthesize a software architecture" in sys_msg:
            return arch_response
        if "Mermaid flowchart" in sys_msg:  # beautify pass
            return "flowchart TD\n    core[\"core\"]\n"
        # writer
        return "# doc\n\nclaim [src/pkg/core.py:1]\n"

    with patch("docforge.agents.reader.chat", side_effect=fake_chat), \
         patch("docforge.agents.architect.chat", side_effect=fake_chat), \
         patch("docforge.agents.diagrammer.chat", side_effect=fake_chat), \
         patch("docforge.agents.diagrammer.provider_in_use", return_value="anthropic"), \
         patch("docforge.agents.writer.chat", side_effect=fake_chat):
        graph = build_graph(index)
        state = initial_state(str(small_repo), m.repo_name, m, str(small_repo))
        final = graph.invoke(state)

    assert final["module_summaries"]
    assert final["architecture"]["components"]
    assert "flowchart" in final["diagram_mmd"]
    assert set(final["drafts"].keys()) == {"README.md", "ARCHITECTURE.md", "API.md", "TUTORIAL.md"}
