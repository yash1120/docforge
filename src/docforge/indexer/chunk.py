"""Tree-sitter-backed function/class chunking with a sliding-window fallback.

Each chunk knows the file and (line_start, line_end) it came from — that's the
foundation that lets Writer/Critic ground every claim with `[file:line]`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tree_sitter import Language, Parser, Node

import tree_sitter_python
import tree_sitter_typescript
import tree_sitter_javascript


@dataclass
class Chunk:
    id: str
    repo: str
    file: str           # repo-relative, forward-slashed
    line_start: int     # 1-indexed, inclusive
    line_end: int       # 1-indexed, inclusive
    lang: str           # "Python" / "TypeScript" / etc.
    kind: str           # "function" / "class" / "method" / "module" / "window"
    name: str | None    # symbol name when kind is function/class/method
    content: str


# ---- Tree-sitter language registry --------------------------------------

_LANGUAGES: dict[str, Language] = {}


def _lang(name: str) -> Language:
    if name in _LANGUAGES:
        return _LANGUAGES[name]
    if name == "Python":
        lang = Language(tree_sitter_python.language())
    elif name == "TypeScript":
        lang = Language(tree_sitter_typescript.language_typescript())
    elif name == "TSX":
        lang = Language(tree_sitter_typescript.language_tsx())
    elif name == "JavaScript":
        lang = Language(tree_sitter_javascript.language())
    else:
        raise KeyError(name)
    _LANGUAGES[name] = lang
    return lang


# Function/class/method node types per language.
_NODE_TYPES: dict[str, dict[str, str]] = {
    "Python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "TypeScript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "arrow_function": "function",
    },
    "TSX": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "arrow_function": "function",
    },
    "JavaScript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "arrow_function": "function",
    },
}

_LANG_BY_EXT: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TSX",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
}

SUPPORTED_LANGS = frozenset(_LANG_BY_EXT.values())


def chunk_file(path: Path, repo_root: Path, repo_name: str) -> list[Chunk]:
    """Chunk one file. Tree-sitter for supported langs; sliding window for the rest."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if not text.strip():
        return []

    rel = str(path.relative_to(repo_root)).replace("\\", "/")
    lang = _LANG_BY_EXT.get(path.suffix.lower())
    if lang and lang in _NODE_TYPES:
        return _tree_sitter_chunks(text, rel, lang, repo_name)
    return _window_chunks(text, rel, _file_lang_label(path), repo_name)


def _tree_sitter_chunks(text: str, rel: str, lang: str, repo: str) -> list[Chunk]:
    parser = Parser(_lang(lang))
    tree = parser.parse(text.encode("utf-8"))
    node_types = _NODE_TYPES[lang]

    chunks: list[Chunk] = []
    lines = text.splitlines()

    for node in _walk_nodes(tree.root_node):
        kind = node_types.get(node.type)
        if not kind:
            continue
        line_start = node.start_point[0] + 1
        line_end = node.end_point[0] + 1
        # Skip empty or trivially small chunks
        if line_end - line_start < 1:
            continue
        body = "\n".join(lines[line_start - 1 : line_end])
        name = _symbol_name(node)
        chunks.append(_make_chunk(repo, rel, line_start, line_end, lang, kind, name, body))

    # Capture top-level statements (imports, constants) that aren't inside any def/class.
    captured_spans = sorted((c.line_start, c.line_end) for c in chunks)
    module_chunks = _gather_uncaptured_top_level(lines, captured_spans, lang, rel, repo)
    chunks.extend(module_chunks)

    if not chunks:
        # File parses but has no defs (e.g., a script) — fall back to a window.
        return _window_chunks(text, rel, lang, repo)
    return chunks


def _walk_nodes(node: Node) -> Iterable[Node]:
    """Iterative tree walk so we never blow the recursion limit on deep files."""
    stack: list[Node] = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(cur.children)


def _symbol_name(node: Node) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node:
        return name_node.text.decode("utf-8", errors="ignore")
    return None


def _gather_uncaptured_top_level(
    lines: list[str], captured: list[tuple[int, int]], lang: str, rel: str, repo: str
) -> list[Chunk]:
    """Collect lines NOT inside any chunked def/class into a single 'module' chunk.

    Useful so retrieval can surface imports + top-level constants for things like
    license headers or framework usage.
    """
    used = [False] * (len(lines) + 1)
    for s, e in captured:
        for i in range(s, min(e + 1, len(used))):
            used[i] = True

    remaining = [(i + 1, line) for i, line in enumerate(lines) if not used[i + 1] and line.strip()]
    if not remaining or len(remaining) < 3:
        return []

    line_start = remaining[0][0]
    line_end = remaining[-1][0]
    body = "\n".join(line for _, line in remaining)
    return [_make_chunk(repo, rel, line_start, line_end, lang, "module", None, body)]


def _window_chunks(text: str, rel: str, lang: str, repo: str) -> list[Chunk]:
    """Sliding-window fallback for unsupported langs or empty parses."""
    WINDOW = 60
    STRIDE = 50
    lines = text.splitlines()
    if not lines:
        return []

    chunks: list[Chunk] = []
    i = 0
    while i < len(lines):
        seg = lines[i : i + WINDOW]
        if not any(l.strip() for l in seg):
            i += STRIDE
            continue
        chunks.append(
            _make_chunk(
                repo, rel, i + 1, min(i + WINDOW, len(lines)), lang, "window", None, "\n".join(seg)
            )
        )
        if len(seg) < WINDOW:
            break
        i += STRIDE
    return chunks


def _file_lang_label(path: Path) -> str:
    """Cheap label for non-tree-sitter files — used in chunk metadata only."""
    return {
        ".md": "Markdown",
        ".rst": "reStructuredText",
        ".yaml": "YAML", ".yml": "YAML",
        ".json": "JSON",
        ".toml": "TOML",
        ".sh": "Shell", ".bash": "Shell",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".rb": "Ruby",
        ".php": "PHP",
        ".sql": "SQL",
        ".html": "HTML",
        ".css": "CSS",
    }.get(path.suffix.lower(), "Text")


def _make_chunk(
    repo: str, rel: str, line_start: int, line_end: int,
    lang: str, kind: str, name: str | None, content: str,
) -> Chunk:
    # ID = stable hash of (repo, file, line_start, content). Lets us re-index
    # without duplicating identical chunks.
    h = hashlib.sha1()
    h.update(f"{repo}|{rel}|{line_start}".encode())
    h.update(content.encode("utf-8", errors="ignore"))
    return Chunk(
        id=h.hexdigest()[:16],
        repo=repo,
        file=rel,
        line_start=line_start,
        line_end=line_end,
        lang=lang,
        kind=kind,
        name=name,
        content=content,
    )
