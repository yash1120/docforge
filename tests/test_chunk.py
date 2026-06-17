"""Chunker tests — fixture files in tmp_path, assert on Chunk metadata + spans."""

from __future__ import annotations

from pathlib import Path


from docforge.indexer.chunk import chunk_file


def _write(p: Path, content: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_python_function_chunk(tmp_path: Path):
    f = _write(
        tmp_path / "demo.py",
        "import os\n\ndef hello(name: str) -> str:\n    return f'hi {name}'\n",
    )
    chunks = chunk_file(f, tmp_path, "demo-repo")
    funcs = [c for c in chunks if c.kind == "function"]
    assert len(funcs) == 1
    assert funcs[0].name == "hello"
    assert funcs[0].lang == "Python"
    assert funcs[0].line_start == 3
    assert "return" in funcs[0].content


def test_python_class_chunk(tmp_path: Path):
    f = _write(
        tmp_path / "demo.py",
        "class Calculator:\n    def add(self, a, b):\n        return a + b\n",
    )
    chunks = chunk_file(f, tmp_path, "demo-repo")
    classes = [c for c in chunks if c.kind == "class"]
    assert len(classes) == 1
    assert classes[0].name == "Calculator"
    assert classes[0].line_start == 1


def test_module_chunk_captures_top_level(tmp_path: Path):
    f = _write(
        tmp_path / "demo.py",
        "import os\nimport sys\nFOO = 1\nBAR = 2\n\ndef work():\n    return FOO + BAR\n",
    )
    chunks = chunk_file(f, tmp_path, "demo-repo")
    kinds = {c.kind for c in chunks}
    assert "module" in kinds
    assert "function" in kinds
    module = next(c for c in chunks if c.kind == "module")
    assert "FOO" in module.content
    assert "import os" in module.content


def test_typescript_function(tmp_path: Path):
    f = _write(
        tmp_path / "demo.ts",
        "export function greet(name: string): string {\n  return `hello ${name}`;\n}\n",
    )
    chunks = chunk_file(f, tmp_path, "demo-repo")
    funcs = [c for c in chunks if c.kind == "function"]
    assert len(funcs) >= 1
    assert any(c.name == "greet" for c in funcs)
    assert all(c.lang == "TypeScript" for c in funcs)


def test_markdown_falls_back_to_window(tmp_path: Path):
    body = "\n".join([f"# Heading {i}" for i in range(20)])
    f = _write(tmp_path / "doc.md", body)
    chunks = chunk_file(f, tmp_path, "demo-repo")
    assert chunks
    assert chunks[0].kind == "window"
    assert chunks[0].lang == "Markdown"


def test_empty_file_yields_no_chunks(tmp_path: Path):
    f = _write(tmp_path / "empty.py", "")
    assert chunk_file(f, tmp_path, "demo-repo") == []


def test_chunk_ids_stable(tmp_path: Path):
    f = _write(
        tmp_path / "stable.py",
        "def f():\n    return 1\n",
    )
    a = chunk_file(f, tmp_path, "r")
    b = chunk_file(f, tmp_path, "r")
    assert [c.id for c in a] == [c.id for c in b]


def test_chunk_ids_change_when_content_changes(tmp_path: Path):
    f = _write(tmp_path / "x.py", "def f():\n    return 1\n")
    before = chunk_file(f, tmp_path, "r")
    f.write_text("def f():\n    return 2\n")
    after = chunk_file(f, tmp_path, "r")
    assert before[0].id != after[0].id


def test_file_path_is_forward_slashed(tmp_path: Path):
    f = _write(tmp_path / "pkg" / "mod.py", "def f(): pass\n")
    chunks = chunk_file(f, tmp_path, "r")
    assert chunks
    assert "/" in chunks[0].file
    assert "\\" not in chunks[0].file
