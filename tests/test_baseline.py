"""Baseline tests — mock the LLM so we test prompt assembly + file selection without a real call."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from docforge.agents.baseline import (
    MAX_FILE_BYTES_EACH,
    render_file_block,
    run_baseline,
    select_files,
)
from docforge.scout import build_manifest


def _make_py_repo(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text('"""pkg docs."""\n')
    (root / "src" / "pkg" / "main.py").write_text(
        'def run() -> None:\n    """Run the thing."""\n    print("hi")\n\n'
        'if __name__ == "__main__":\n    run()\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\ndependencies = ["fastapi"]\n'
    )
    (root / "README.md").write_text("# pkg\nold docs\n")
    return root


def test_select_files_picks_entry_points_first(tmp_path: Path):
    repo = _make_py_repo(tmp_path)
    m = build_manifest(repo)
    files = select_files(m)
    rel = [str(f.relative_to(repo)).replace("\\", "/") for f in files]
    assert any("main.py" in r for r in rel)


def test_render_file_block_truncates_large_files(tmp_path: Path):
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 5000)  # ~30 KB, well above MAX_FILE_BYTES_EACH
    block = render_file_block(tmp_path, big)
    assert "truncated for prompt size" in block
    assert len(block) < MAX_FILE_BYTES_EACH + 500


def test_render_file_block_uses_forward_slash(tmp_path: Path):
    nested = tmp_path / "a" / "b.py"
    nested.parent.mkdir()
    nested.write_text("x = 1\n")
    block = render_file_block(tmp_path, nested)
    assert "a/b.py" in block
    assert "a\\b.py" not in block


def test_run_baseline_assembles_prompt_with_manifest_and_files(tmp_path: Path):
    repo = _make_py_repo(tmp_path)
    m = build_manifest(repo)

    captured = {}

    def fake_chat(messages, **_):
        captured["messages"] = messages
        return "# pkg\n\nA real README.\n"

    with patch("docforge.agents.baseline.chat", side_effect=fake_chat):
        result = run_baseline(m)

    assert result.markdown.startswith("# pkg")
    assert "main.py" in [Path(f).name for f in result.files_included]
    # System + user message
    msgs = captured["messages"]
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"
    assert "pkg" in msgs[1].content
    # Manifest JSON is embedded so the model sees the stack
    assert "FastAPI" in msgs[1].content or "fastapi" in msgs[1].content


def test_run_baseline_handles_repo_with_no_readme(tmp_path: Path):
    (tmp_path / "a.py").write_text("def f(): pass\n")
    m = build_manifest(tmp_path)

    def fake_chat(_, **__):
        return "# Generated\n"

    with patch("docforge.agents.baseline.chat", side_effect=fake_chat):
        result = run_baseline(m)
    assert result.markdown.startswith("# Generated")
