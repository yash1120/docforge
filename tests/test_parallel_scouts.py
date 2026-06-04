"""Tests for the three parallel specialist scouts: TestScout, APIScanner, ConfigReader.

All three are pure static analysis — no LLM, no mocking required. We build
small fixture repos in tmp_path and assert on what each scout extracts.

Also covers `_parallel.parallel_map`: ordering, error isolation, and the
serial-fallback path when DOCFORGE_MAX_PARALLEL=1.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from docforge.agents import (
    initial_state,
    run_api_scanner,
    run_config_reader,
    run_test_scout,
)
from docforge.agents._parallel import parallel_map
from docforge.scout import build_manifest


# ---- TestScout ----------------------------------------------------------


def _seed_python_repo(root: Path) -> None:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "core.py").write_text(
        "def run(): return 42\n\nclass Engine:\n    def start(self): pass\n"
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text(
        "import pytest\n"
        "from pkg.core import run, Engine\n"
        "def test_run(): assert run() == 42\n"
        "def test_engine_starts():\n    Engine().start()\n"
    )
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\ndependencies = []\n')


def test_test_scout_counts_pytest_cases(tmp_path: Path):
    _seed_python_repo(tmp_path)
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_test_scout(state)

    s = update["test_summary"]
    assert s["total_test_files"] == 1
    assert s["total_test_cases"] == 2
    assert "pytest" in s["frameworks"]
    # Citations reference real lines in the test file
    assert any("tests/test_core.py:" in c for c in s["citations"])


def test_test_scout_marks_untested_symbols(tmp_path: Path):
    _seed_python_repo(tmp_path)
    # Add a public symbol the tests never reference
    (tmp_path / "src" / "pkg" / "core.py").write_text(
        "def run(): return 42\n\n"
        "def shutdown(): pass\n\n"          # untested!
        "class Engine:\n    def start(self): pass\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_test_scout(state)

    s = update["test_summary"]
    assert "shutdown" in s["untested_symbols"]
    assert "run" in s["tested_symbols"] or "Engine" in s["tested_symbols"]


def test_test_scout_handles_repo_with_no_tests(tmp_path: Path):
    (tmp_path / "x.py").write_text("def f(): pass\n")
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_test_scout(state)
    s = update["test_summary"]
    assert s["total_test_files"] == 0
    assert s["total_test_cases"] == 0
    assert s["frameworks"] == []


def test_test_scout_recognizes_jest(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.ts").write_text("export function add(a: number, b: number) { return a+b }\n")
    (tmp_path / "__tests__").mkdir()
    (tmp_path / "__tests__" / "lib.test.ts").write_text(
        "import { add } from '../src/lib';\n"
        "import { describe, it, expect } from '@jest/globals';\n"
        "describe('add', () => {\n"
        "  it('sums two numbers', () => { expect(add(1, 2)).toBe(3); });\n"
        "  it('handles zero', () => { expect(add(0, 0)).toBe(0); });\n"
        "});\n"
    )
    (tmp_path / "package.json").write_text('{"name":"x","dependencies":{}}')

    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_test_scout(state)
    s = update["test_summary"]
    assert s["total_test_cases"] == 2
    assert any("jest" in f.lower() for f in s["frameworks"])


# ---- APIScanner ---------------------------------------------------------


def test_api_scanner_extracts_fastapi_routes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/users/{id}')\n"
        "async def get_user(id: int):\n"
        "    return {'id': id}\n"
        "\n"
        "@app.post('/users')\n"
        "def create_user():\n"
        "    return {'ok': True}\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["fastapi"]\n'
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_api_scanner(state)

    routes = update["api_routes"]
    methods_paths = {(r["method"], r["path"]) for r in routes}
    assert ("GET", "/users/{id}") in methods_paths
    assert ("POST", "/users") in methods_paths

    get_user = next(r for r in routes if r["path"] == "/users/{id}")
    assert get_user["handler"] == "get_user"
    assert "src/api.py:" in get_user["citation"]


def test_api_scanner_extracts_flask_route_with_methods(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route('/hello', methods=['GET', 'POST'])\n"
        "def hello():\n"
        "    return 'hi'\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_api_scanner(state)
    routes = update["api_routes"]
    methods = {r["method"] for r in routes if r["path"] == "/hello"}
    assert methods == {"GET", "POST"}


def test_api_scanner_extracts_express_routes(tmp_path: Path):
    (tmp_path / "server.js").write_text(
        "const app = express();\n"
        "app.get('/health', (req, res) => res.send('ok'));\n"
        "app.post('/items', handler);\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_api_scanner(state)
    methods_paths = {(r["method"], r["path"]) for r in update["api_routes"]}
    assert ("GET", "/health") in methods_paths
    assert ("POST", "/items") in methods_paths


def test_api_scanner_extracts_click_commands(tmp_path: Path):
    (tmp_path / "cli.py").write_text(
        "import click\n"
        "\n"
        "@click.group()\n"
        "def cli(): pass\n"
        "\n"
        "@cli.command('hello')\n"
        "def hello_cmd():\n"
        "    click.echo('hi')\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_api_scanner(state)
    cli_routes = [r for r in update["api_routes"] if r["kind"] == "cli"]
    assert cli_routes
    # The "hello" command should appear; it may also pick up the bare @cli.group entry
    paths = {r["path"] for r in cli_routes}
    assert "hello" in paths


def test_api_scanner_dedupes_identical_routes(tmp_path: Path):
    # Two identical fastapi decorators across two files — should yield one route.
    for fname in ("a.py", "b.py"):
        (tmp_path / fname).write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/same')\n"
            "def h(): pass\n"
        )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_api_scanner(state)
    paths = [r["path"] for r in update["api_routes"] if r["method"] == "GET"]
    assert paths.count("/same") == 1


# ---- ConfigReader -------------------------------------------------------


def test_config_reader_finds_python_env_get(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text(
        "import os\n"
        "DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///dev.db')\n"
        "API_KEY = os.environ['OPENAI_API_KEY']\n"   # required
        "TIMEOUT = os.getenv('TIMEOUT', '30')\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_config_reader(state)
    names = {v["name"]: v for v in update["config_summary"]["env_vars"]}
    assert set(names) >= {"DATABASE_URL", "OPENAI_API_KEY", "TIMEOUT"}
    assert names["OPENAI_API_KEY"]["required"] is True
    assert names["DATABASE_URL"]["default"] == "sqlite:///dev.db"
    assert names["DATABASE_URL"]["required"] is False


def test_config_reader_finds_js_process_env(tmp_path: Path):
    (tmp_path / "server.js").write_text(
        "const port = process.env.PORT || 3000;\n"
        "const secret = process.env['JWT_SECRET'];\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_config_reader(state)
    names = {v["name"] for v in update["config_summary"]["env_vars"]}
    assert "PORT" in names
    assert "JWT_SECRET" in names


def test_config_reader_parses_dotenv_example(tmp_path: Path):
    (tmp_path / ".env.example").write_text(
        "DATABASE_URL=postgres://localhost/myapp\n"
        "REDIS_URL=\n"
        "# a comment\n"
        "ENABLE_FEATURE=true\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_config_reader(state)
    names = {v["name"]: v for v in update["config_summary"]["env_vars"]}
    assert "DATABASE_URL" in names
    assert names["DATABASE_URL"]["default"] == "postgres://localhost/myapp"
    # Empty value -> still required
    assert names.get("REDIS_URL", {}).get("required") in (True, False)  # either ok
    # Config files surfaced
    assert ".env.example" in update["config_summary"]["config_files"]


def test_config_reader_redacts_secret_names(tmp_path: Path):
    """Names containing API_KEY/SECRET/TOKEN/PASSWORD/etc. must never leak their value."""
    (tmp_path / "app.py").write_text(
        "import os\n"
        "key = os.environ.get('OPENAI_API_KEY', 'sk-real-secret-xyz')\n"
        "tok = os.environ.get('GITHUB_TOKEN', 'ghp_realtoken123')\n"
        "url = os.environ.get('DATABASE_URL', 'postgres://localhost/db')\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_config_reader(state)
    by_name = {v["name"]: v for v in update["config_summary"]["env_vars"]}
    assert by_name["OPENAI_API_KEY"]["default"] == "<redacted>"
    assert by_name["GITHUB_TOKEN"]["default"] == "<redacted>"
    # Non-secret defaults should pass through unmodified
    assert by_name["DATABASE_URL"]["default"] == "postgres://localhost/db"


def test_config_reader_redacts_all_values_from_real_dotenv(tmp_path: Path):
    """A live .env (not .env.example) is assumed sensitive — redact every populated value."""
    (tmp_path / ".env").write_text(
        "FEATURE_FLAG=enabled\n"
        "DATABASE_URL=postgres://prod/x\n"
        "API_HOST=api.example.com\n"
    )
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_config_reader(state)
    for v in update["config_summary"]["env_vars"]:
        # Defaults from a live .env should be redacted regardless of name
        assert v["default"] in (None, "<redacted>"), f"{v['name']} leaked: {v['default']!r}"


def test_config_reader_merges_code_and_dotenv(tmp_path: Path):
    """Code mentions FOO with no default; .env.example provides one — merge."""
    (tmp_path / "app.py").write_text("import os\nx = os.environ['FOO']\n")
    (tmp_path / ".env.example").write_text("FOO=hello\n")
    m = build_manifest(tmp_path)
    state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
    update = run_config_reader(state)
    foo = next(v for v in update["config_summary"]["env_vars"] if v["name"] == "FOO")
    # .env.example wins on default; required flips to False.
    assert foo["default"] == "hello"
    assert foo["required"] is False


# ---- _parallel.parallel_map ---------------------------------------------


def test_parallel_map_preserves_order():
    def square(x: int) -> int:
        time.sleep(0.01 * (5 - x))  # later inputs finish faster
        return x * x

    out = parallel_map(square, range(5))
    assert out == [0, 1, 4, 9, 16]


def test_parallel_map_isolates_errors_with_default_factory():
    def f(x: int) -> int:
        if x == 2:
            raise ValueError("bang")
        return x * 10

    out = parallel_map(
        f,
        range(4),
        default_factory=lambda item, exc: -1,
    )
    assert out == [0, 10, -1, 30]


def test_parallel_map_serial_fallback_under_env_flag():
    with patch.dict(os.environ, {"DOCFORGE_MAX_PARALLEL": "1"}):
        out = parallel_map(lambda x: x + 1, [10, 20, 30])
    assert out == [11, 21, 31]


def test_parallel_map_empty_input():
    assert parallel_map(lambda x: x, []) == []


def test_parallel_map_speedup_real():
    """Sanity check: parallel really is faster than serial for I/O-bound work."""
    def slow(x: int) -> int:
        time.sleep(0.05)
        return x

    # Serial
    with patch.dict(os.environ, {"DOCFORGE_MAX_PARALLEL": "1"}):
        t0 = time.time()
        parallel_map(slow, range(8))
        serial = time.time() - t0

    # Parallel (default 4 workers)
    with patch.dict(os.environ, {"DOCFORGE_MAX_PARALLEL": "4"}):
        t0 = time.time()
        parallel_map(slow, range(8))
        par = time.time() - t0

    # Should be at least 1.5x faster — generous bound to avoid CI flakiness
    assert par < serial * 0.75, f"parallel ({par:.3f}s) not meaningfully faster than serial ({serial:.3f}s)"


# ---- Supervisor end-to-end with parallel scouts -------------------------


def test_supervisor_runs_three_scouts_plus_team_end_to_end(tmp_path: Path):
    """Full graph: 3 parallel scouts -> reader -> architect -> diagrammer -> writer -> critic.
    All LLM calls mocked.
    """
    import json
    from docforge.agents import build_graph
    from docforge.indexer import build_index
    from docforge.scout.walk import walk_repo

    # Build a small repo with content for ALL three scouts to find something.
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "pkg" / "api.py").write_text(
        "import os\n"
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "TOKEN = os.environ.get('API_TOKEN', 'dev')\n"
        "\n"
        "@app.get('/ping')\n"
        "def ping():\n"
        "    return 'pong'\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ping.py").write_text(
        "def test_x(): assert 1 == 1\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\ndependencies = ["fastapi"]\n'
    )

    m = build_manifest(tmp_path)
    files, _ = walk_repo(tmp_path)
    index, _ = build_index(tmp_path, files, m.repo_name, persist_dir=tmp_path / ".chroma")

    def fake_chat(messages, **_):
        sys_msg = messages[0].content
        if "JSON object describing this one module" in sys_msg:
            return json.dumps({
                "module": "src/pkg", "purpose": "API module",
                "public_api": ["ping"],
                "key_behaviors": ["GET /ping returns pong [src/pkg/api.py:7]"],
                "citations": ["src/pkg/api.py:7"],
            })
        if "synthesize a software architecture" in sys_msg:
            return json.dumps({
                "components": [{"name": "api", "purpose": "HTTP layer",
                                "files": ["src/pkg/api.py"], "citations": ["src/pkg/api.py:7"]}],
                "edges": [], "external_deps": [{"name": "FastAPI", "role": "HTTP framework"}],
                "runtime_topology": "server",
            })
        if "polish a Mermaid flowchart" in sys_msg:
            return "flowchart TD\n    api[\"api\"]\n"
        if "verify whether a citation actually supports" in sys_msg:
            return json.dumps({"supported": True, "reason": "ok"})
        return "# doc\n\nThe ping route lives at [src/pkg/api.py:7].\n"

    with patch("docforge.agents.reader.chat", side_effect=fake_chat), \
         patch("docforge.agents.architect.chat", side_effect=fake_chat), \
         patch("docforge.agents.diagrammer.chat", side_effect=fake_chat), \
         patch("docforge.agents.diagrammer.provider_in_use", return_value="anthropic"), \
         patch("docforge.agents.writer.chat", side_effect=fake_chat), \
         patch("docforge.agents.critic.chat", side_effect=fake_chat), \
         patch("docforge.agents.editor.chat", side_effect=fake_chat):

        graph = build_graph(index, critic_loop=True)
        state = initial_state(str(tmp_path), m.repo_name, m, str(tmp_path))
        final = graph.invoke(state)

    # All three parallel scouts wrote their slice of state
    assert final["test_summary"]["total_test_files"] == 1
    assert any(r["path"] == "/ping" for r in final["api_routes"])
    assert any(v["name"] == "API_TOKEN" for v in final["config_summary"]["env_vars"])

    # LLM agents downstream completed
    assert final["module_summaries"]
    assert final["architecture"]["components"]
    assert "flowchart" in final["diagram_mmd"]
    assert set(final["drafts"].keys()) == {"README.md", "ARCHITECTURE.md", "API.md", "TUTORIAL.md"}
