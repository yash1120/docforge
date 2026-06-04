"""Scout tests — build small repo fixtures in tmp_path, then assert on the manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docforge.scout import build_manifest


@pytest.fixture
def py_repo(tmp_path: Path) -> Path:
    (tmp_path / "src" / "demo").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "__init__.py").write_text("")
    (tmp_path / "src" / "demo" / "main.py").write_text(
        'def add(a: int, b: int) -> int:\n    return a + b\n\nclass Calculator:\n    pass\n\n'
        'if __name__ == "__main__":\n    print(add(1, 2))\n'
    )
    (tmp_path / "src" / "demo" / "_private.py").write_text("def _helper(): pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_demo.py").write_text("def test_add(): assert 1+1==2\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        'dependencies = ["fastapi>=0.100", "pydantic>=2.0", "click"]\n'
    )
    (tmp_path / "LICENSE").write_text(
        "MIT License\n\nPermission is hereby granted, free of charge, to any person...\n"
    )
    (tmp_path / "README.md").write_text("# demo\n")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11\n")
    return tmp_path


def test_detects_python_as_primary(py_repo: Path):
    m = build_manifest(py_repo)
    assert m.primary_language == "Python"
    assert m.languages["Python"] >= 3


def test_detects_frameworks_from_pyproject(py_repo: Path):
    m = build_manifest(py_repo)
    assert "FastAPI" in m.frameworks
    assert "Pydantic" in m.frameworks
    assert "Click" in m.frameworks


def test_detects_entry_point_main(py_repo: Path):
    m = build_manifest(py_repo)
    assert any("main.py" in ep for ep in m.entry_points)


def test_detects_top_level_module(py_repo: Path):
    m = build_manifest(py_repo)
    assert "src/demo" in m.top_level_modules


def test_detects_license_mit(py_repo: Path):
    m = build_manifest(py_repo)
    assert m.license == "MIT"
    assert m.license_file == "LICENSE"


def test_detects_tests_and_docker(py_repo: Path):
    m = build_manifest(py_repo)
    assert m.has_tests is True
    assert m.has_docker is True


def test_public_api_excludes_tests_and_private(py_repo: Path):
    m = build_manifest(py_repo)
    symbols = m.public_api
    # Public class + function should appear
    assert any("Calculator" in s for s in symbols)
    assert any("::add" in s for s in symbols)
    # Test files and private files should not
    assert not any("tests/" in s for s in symbols)
    assert not any("_private" in s for s in symbols)


def test_skips_venv_and_node_modules(tmp_path: Path):
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "junk.py").write_text("x = 1\n")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "junk.js").write_text("console.log(1)\n")
    (tmp_path / "real.py").write_text("def f(): pass\n")
    m = build_manifest(tmp_path)
    # Only real.py should be counted
    assert m.languages.get("Python", 0) == 1
    assert m.languages.get("JavaScript", 0) == 0


def test_manifest_is_json_serializable(py_repo: Path):
    m = build_manifest(py_repo)
    payload = json.dumps(m.model_dump())
    assert "demo" in payload
