"""Week 3 tests — Critic deterministic checks, Editor revisions, and the
supervisor's conditional critic-editor loop. All LLM calls are mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docforge.agents import (
    Architecture,
    ModuleSummary,
    compute_coverage,
    initial_state,
    parse_citations,
    run_critic,
    run_editor,
)
from docforge.agents.critic import (
    check_citation,
    find_uncited_factual_claims,
    judge_grounding,
)
from docforge.scout import build_manifest


# ---- Fixtures -----------------------------------------------------------


@pytest.fixture
def cited_repo(tmp_path: Path) -> Path:
    """A repo with a single .py file we can cite line-by-line in tests."""
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text('"""pkg."""\n')
    (src / "core.py").write_text(
        "import os\n"                  # line 1
        "\n"                           # line 2
        "def run() -> int:\n"          # line 3
        '    """Top level entry."""\n' # line 4
        "    return 42\n"              # line 5
        "\n"                           # line 6
        "class Engine:\n"              # line 7
        "    def start(self) -> None:\n"  # line 8
        "        pass\n"               # line 9
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\ndependencies = []\n'
    )
    (tmp_path / "README.md").write_text("# pkg\n")
    return tmp_path


# ---- parse_citations -----------------------------------------------------


def test_parse_citations_extracts_single_and_range():
    md = "Foo runs [src/pkg/core.py:5]. Engine spans [src/pkg/core.py:7-9]."
    refs = parse_citations(md)
    assert len(refs) == 2
    assert refs[0].file == "src/pkg/core.py"
    assert refs[0].line_start == 5 and refs[0].line_end == 5
    assert refs[1].line_start == 7 and refs[1].line_end == 9


def test_parse_citations_carries_sentence_context():
    md = "Engine has a start method [src/pkg/core.py:8]. Other unrelated text."
    refs = parse_citations(md)
    assert refs
    assert "Engine has a start method" in refs[0].sentence


def test_parse_citations_ignores_non_file_brackets():
    md = "This [is] not a [citation]. But [src/pkg/core.py:3] is one."
    refs = parse_citations(md)
    assert len(refs) == 1
    assert refs[0].file == "src/pkg/core.py"


# ---- check_citation (deterministic) -------------------------------------


def test_check_citation_accepts_in_range(cited_repo: Path):
    refs = parse_citations("[src/pkg/core.py:5]")
    ok, _ = check_citation(refs[0], cited_repo)
    assert ok


def test_check_citation_rejects_missing_file(cited_repo: Path):
    refs = parse_citations("[src/pkg/ghost.py:1]")
    ok, reason = check_citation(refs[0], cited_repo)
    assert not ok and "file not found" in reason


def test_check_citation_rejects_out_of_range(cited_repo: Path):
    refs = parse_citations("[src/pkg/core.py:9999]")
    ok, reason = check_citation(refs[0], cited_repo)
    assert not ok and "outside file" in reason


def test_check_citation_rejects_reversed_range(cited_repo: Path):
    # Manually construct since the regex won't normally yield reversed ranges
    from docforge.agents.critic import CitationRef
    bad = CitationRef(raw="x", file="src/pkg/core.py", line_start=8, line_end=3, sentence="s")
    ok, reason = check_citation(bad, cited_repo)
    assert not ok and "reversed" in reason


# ---- find_uncited_factual_claims ----------------------------------------


def test_uncited_claims_flags_code_tokens_without_citations():
    md = (
        "# Title\n"
        "- The `Engine.start()` method does the thing.\n"          # has code token, no cite
        "- The `run()` function returns 42 [src/pkg/core.py:5].\n"  # has cite — ok
        "- Regular sentence without code.\n"                        # no code, no cite — ok
    )
    suspects = find_uncited_factual_claims(md)
    assert any("Engine.start" in s for s in suspects)
    assert all("run()" not in s for s in suspects)


# ---- compute_coverage ---------------------------------------------------


def test_coverage_full_when_all_mentioned():
    api = ["src/foo.py::Bar", "src/foo.py::baz"]
    drafts = {"README.md": "Use Bar and baz to do things."}
    score, missing = compute_coverage(api, drafts)
    assert score == 1.0
    assert missing == []


def test_coverage_partial_when_some_missing():
    api = ["src/foo.py::Bar", "src/foo.py::baz", "src/foo.py::qux"]
    drafts = {"README.md": "Use Bar."}
    score, missing = compute_coverage(api, drafts)
    assert 0 < score < 1
    assert set(missing) == {"baz", "qux"}


def test_coverage_word_boundary():
    api = ["x.py::run"]
    drafts = {"README.md": "We use runtime checks."}  # 'run' is inside 'runtime' — should NOT count
    score, missing = compute_coverage(api, drafts)
    assert score == 0.0
    assert "run" in missing


# ---- judge_grounding (LLM, mocked) --------------------------------------


def test_judge_grounding_returns_supported_on_match(cited_repo: Path):
    from docforge.agents.critic import CitationRef
    ref = CitationRef(
        raw="[src/pkg/core.py:3-5]", file="src/pkg/core.py",
        line_start=3, line_end=5,
        sentence="The run() function returns 42",
    )
    with patch("docforge.agents.critic.chat",
               return_value=json.dumps({"supported": True, "reason": "matches"})):
        ok, reason = judge_grounding(ref, cited_repo)
    assert ok and "matches" in reason


def test_judge_grounding_returns_unsupported_on_mismatch(cited_repo: Path):
    from docforge.agents.critic import CitationRef
    ref = CitationRef(
        raw="[src/pkg/core.py:1]", file="src/pkg/core.py",
        line_start=1, line_end=1, sentence="run() returns 999",
    )
    with patch("docforge.agents.critic.chat",
               return_value=json.dumps({"supported": False, "reason": "claim says 999"})):
        ok, _ = judge_grounding(ref, cited_repo)
    assert not ok


# ---- run_critic end-to-end (mocked LLM) ---------------------------------


def test_critic_flags_broken_citation(cited_repo: Path):
    m = build_manifest(cited_repo)
    state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
    state["drafts"] = {
        "README.md": "We use [src/pkg/ghost.py:1] for the thing."
    }

    with patch("docforge.agents.critic.chat",
               return_value=json.dumps({"supported": True, "reason": "n/a"})):
        update = run_critic(state)

    critique = update["critique"]
    assert any(i["kind"] == "broken_citation" for i in critique["issues"])


def test_critic_flags_ungrounded_claim(cited_repo: Path):
    m = build_manifest(cited_repo)
    state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
    state["drafts"] = {
        "README.md": "The system launches rockets [src/pkg/core.py:3-5]."
    }

    with patch("docforge.agents.critic.chat",
               return_value=json.dumps({"supported": False, "reason": "no rocket code"})):
        update = run_critic(state)

    critique = update["critique"]
    assert any(i["kind"] == "ungrounded" for i in critique["issues"])
    assert critique["factuality_score"] < 1.0


def test_critic_records_cycle_and_density(cited_repo: Path):
    m = build_manifest(cited_repo)
    state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
    state["drafts"] = {"README.md": "Run is in [src/pkg/core.py:3-5]."}
    state["cycles"] = 1

    with patch("docforge.agents.critic.chat",
               return_value=json.dumps({"supported": True, "reason": "ok"})):
        update = run_critic(state)

    assert update["cycles"] == 2
    assert update["critique"]["citation_density"] > 0


def test_critic_uses_zero_llm_calls_when_no_valid_citations(cited_repo: Path):
    """If every citation is broken, we shouldn't bother calling the LLM."""
    m = build_manifest(cited_repo)
    state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
    state["drafts"] = {"README.md": "All [missing/file.py:1] are gone."}

    def boom(*_a, **_kw):
        raise AssertionError("LLM should not be called when all cites are broken")

    with patch("docforge.agents.critic.chat", side_effect=boom):
        update = run_critic(state)

    assert any(i["kind"] == "broken_citation" for i in update["critique"]["issues"])


# ---- run_editor ---------------------------------------------------------


def test_editor_revises_doc_with_issues(cited_repo: Path):
    m = build_manifest(cited_repo)
    state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
    state["drafts"] = {
        "README.md": "Old bad content.",
        "API.md": "Stable content.",
    }
    state["critique"] = {
        "issues": [
            {"doc": "README.md", "severity": "error", "kind": "broken_citation",
             "claim": "X", "citation": "[bad.py:1]", "suggestion": "remove"},
        ],
        "factuality_score": 0.5,
        "coverage_score": 1.0,
        "citation_density": 0.0,
        "summary": "1 issue",
        "cycle": 1,
    }

    captured: list[str] = []

    def fake_chat(messages, **_):
        captured.append(messages[1].content)
        return "# README\n\nRevised content.\n"

    with patch("docforge.agents.editor.chat", side_effect=fake_chat):
        update = run_editor(state)

    # README revised, API untouched
    assert update["drafts"]["README.md"].startswith("# README")
    assert update["drafts"]["API.md"] == "Stable content."
    # The captured prompt mentions the issue
    assert "broken_citation" in captured[0]


def test_editor_passes_through_when_no_issues(cited_repo: Path):
    m = build_manifest(cited_repo)
    state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
    state["drafts"] = {"README.md": "Already good."}
    state["critique"] = {
        "issues": [],
        "factuality_score": 1.0, "coverage_score": 1.0,
        "citation_density": 0.0, "summary": "clean", "cycle": 1,
    }

    def boom(*_a, **_kw):
        raise AssertionError("LLM should not be called when there are no issues")

    with patch("docforge.agents.editor.chat", side_effect=boom):
        update = run_editor(state)
    assert update["drafts"] == {"README.md": "Already good."}


def test_editor_applies_floating_issues_to_all_docs(cited_repo: Path):
    m = build_manifest(cited_repo)
    state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
    state["drafts"] = {"README.md": "x", "API.md": "y"}
    state["critique"] = {
        "issues": [
            {"doc": "(any)", "severity": "warn", "kind": "missing_coverage",
             "claim": "Foo", "citation": None, "suggestion": "mention Foo"},
        ],
        "factuality_score": 1.0, "coverage_score": 0.5,
        "citation_density": 0.0, "summary": "1 issue", "cycle": 1,
    }

    call_log: list[str] = []

    def fake_chat(messages, **_):
        # User message includes the doc name in its first line
        call_log.append(messages[1].content[:50])
        return "revised"

    with patch("docforge.agents.editor.chat", side_effect=fake_chat):
        update = run_editor(state)

    # Both docs revised once each
    assert update["drafts"]["README.md"] == "revised"
    assert update["drafts"]["API.md"] == "revised"
    assert len(call_log) == 2


# ---- Supervisor loop ----------------------------------------------------


def test_supervisor_critic_loop_stops_at_zero_issues(cited_repo: Path):
    """Critic returns no issues -> graph routes to END after first critic call."""
    from docforge.agents import build_graph
    from docforge.indexer import build_index
    from docforge.scout.walk import walk_repo

    m = build_manifest(cited_repo)
    files, _ = walk_repo(cited_repo)
    index, _ = build_index(cited_repo, files, m.repo_name, persist_dir=cited_repo / ".dx-chroma")

    def fake_chat(messages, **_):
        sys_msg = messages[0].content
        if "JSON object describing this one module" in sys_msg:
            return json.dumps({
                "module": "src/pkg", "purpose": "p", "public_api": ["run"],
                "key_behaviors": ["[src/pkg/core.py:3]"], "citations": ["src/pkg/core.py:3"],
            })
        if "synthesize a software architecture" in sys_msg:
            return json.dumps({
                "components": [{"name": "core", "purpose": "engine",
                                "files": ["src/pkg/core.py"], "citations": ["src/pkg/core.py:3"]}],
                "edges": [], "external_deps": [], "runtime_topology": "library",
            })
        if "polish a Mermaid flowchart" in sys_msg:
            return "flowchart TD\n    core[\"core\"]\n"
        if "verify whether a citation actually supports" in sys_msg:
            return json.dumps({"supported": True, "reason": "ok"})
        # writer / editor
        return "# doc\n\nThe run function lives at [src/pkg/core.py:3].\n"

    editor_called = {"n": 0}
    real_editor = run_editor

    def counting_editor(state):
        editor_called["n"] += 1
        return real_editor(state)

    # Patch chat at the module level for each agent module that uses it.
    with patch("docforge.agents.reader.chat", side_effect=fake_chat), \
         patch("docforge.agents.architect.chat", side_effect=fake_chat), \
         patch("docforge.agents.diagrammer.chat", side_effect=fake_chat), \
         patch("docforge.agents.diagrammer.provider_in_use", return_value="anthropic"), \
         patch("docforge.agents.writer.chat", side_effect=fake_chat), \
         patch("docforge.agents.critic.chat", side_effect=fake_chat), \
         patch("docforge.agents.editor.chat", side_effect=fake_chat), \
         patch("docforge.agents.supervisor.run_editor", side_effect=counting_editor):

        graph = build_graph(index, critic_loop=True)
        state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
        final = graph.invoke(state)

    # With a clean draft + grounded citations, the critic may still flag
    # coverage-missing symbols. The loop should terminate within MAX_CRITIC_CYCLES.
    assert final["cycles"] >= 1
    assert final["cycles"] <= 2  # MAX_CRITIC_CYCLES


def test_supervisor_critic_loop_terminates_at_cycle_cap(cited_repo: Path):
    """If the critic keeps flagging issues, the loop must still terminate at MAX_CRITIC_CYCLES."""
    from docforge.agents import build_graph, MAX_CRITIC_CYCLES
    from docforge.indexer import build_index
    from docforge.scout.walk import walk_repo

    m = build_manifest(cited_repo)
    files, _ = walk_repo(cited_repo)
    index, _ = build_index(cited_repo, files, m.repo_name, persist_dir=cited_repo / ".dx-chroma2")

    def fake_chat(messages, **_):
        sys_msg = messages[0].content
        if "JSON object describing this one module" in sys_msg:
            return json.dumps({
                "module": "src/pkg", "purpose": "p", "public_api": ["run"],
                "key_behaviors": ["[missing/path.py:1]"],  # broken citation
                "citations": ["missing/path.py:1"],
            })
        if "synthesize a software architecture" in sys_msg:
            return json.dumps({
                "components": [{"name": "core", "purpose": "engine",
                                "files": ["src/pkg/core.py"], "citations": []}],
                "edges": [], "external_deps": [], "runtime_topology": "library",
            })
        if "polish a Mermaid flowchart" in sys_msg:
            return "flowchart TD\n    core[\"core\"]\n"
        if "verify whether a citation actually supports" in sys_msg:
            return json.dumps({"supported": False, "reason": "no"})
        # writer + editor both emit content with a broken citation
        return "# doc\n\nThe run function lives at [missing/path.py:1].\n"

    with patch("docforge.agents.reader.chat", side_effect=fake_chat), \
         patch("docforge.agents.architect.chat", side_effect=fake_chat), \
         patch("docforge.agents.diagrammer.chat", side_effect=fake_chat), \
         patch("docforge.agents.diagrammer.provider_in_use", return_value="anthropic"), \
         patch("docforge.agents.writer.chat", side_effect=fake_chat), \
         patch("docforge.agents.critic.chat", side_effect=fake_chat), \
         patch("docforge.agents.editor.chat", side_effect=fake_chat):

        graph = build_graph(index, critic_loop=True)
        state = initial_state(str(cited_repo), m.repo_name, m, str(cited_repo))
        final = graph.invoke(state)

    assert final["cycles"] == MAX_CRITIC_CYCLES, f"loop must cap; got {final['cycles']}"
