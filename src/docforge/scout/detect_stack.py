from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

# Extension -> language label
EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".cs": "C#",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "SCSS",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
}

# Source files that count toward LOC totals (vs config/docs)
CODE_LANGS = frozenset({
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "Java", "Kotlin",
    "Scala", "Ruby", "PHP", "Swift", "C#", "C", "C++", "Vue", "Svelte",
})

PY_FRAMEWORK_HINTS: dict[str, str] = {
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
    "starlette": "Starlette", "uvicorn": "Uvicorn",
    "langgraph": "LangGraph", "langchain": "LangChain",
    "click": "Click", "typer": "Typer",
    "pydantic": "Pydantic", "sqlalchemy": "SQLAlchemy",
    "pytest": "pytest",
    "groq": "Groq", "anthropic": "Anthropic", "openai": "OpenAI",
    "chromadb": "Chroma", "qdrant-client": "Qdrant",
    "sentence-transformers": "sentence-transformers",
    "tree-sitter": "tree-sitter",
    "torch": "PyTorch", "tensorflow": "TensorFlow",
    "numpy": "NumPy", "pandas": "pandas", "scikit-learn": "scikit-learn",
}
JS_FRAMEWORK_HINTS: dict[str, str] = {
    "react": "React", "vue": "Vue", "svelte": "Svelte",
    "next": "Next.js", "nuxt": "Nuxt", "remix": "Remix",
    "express": "Express", "koa": "Koa", "fastify": "Fastify",
    "@nestjs/core": "NestJS",
    "tailwindcss": "Tailwind", "vite": "Vite", "webpack": "webpack",
    "typescript": "TypeScript",
}


def detect_languages(
    files: list[Path], root: Path
) -> tuple[dict[str, int], Optional[str], int, int]:
    counts: dict[str, int] = {}
    total_loc = 0
    code_files = 0
    for f in files:
        lang = EXT_TO_LANG.get(f.suffix.lower())
        if not lang:
            continue
        counts[lang] = counts.get(lang, 0) + 1
        if lang in CODE_LANGS:
            code_files += 1
            try:
                with f.open("r", encoding="utf-8", errors="ignore") as fp:
                    total_loc += sum(1 for _ in fp)
            except OSError:
                pass

    # Primary = most common CODE language (not Markdown/YAML)
    code_counts = {k: v for k, v in counts.items() if k in CODE_LANGS}
    primary = max(code_counts, key=code_counts.get) if code_counts else None
    return counts, primary, total_loc, code_files


def detect_dependencies(
    root: Path,
) -> tuple[list[str], dict[str, list[str]], list[Path]]:
    """Returns (framework labels, {source_file: [pkg, ...]}, dep file paths)."""
    frameworks: set[str] = set()
    deps: dict[str, list[str]] = {}
    files: list[Path] = []

    # Python — pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        files.append(pyproject)
        try:
            text = pyproject.read_text(encoding="utf-8", errors="ignore")
            pkgs = _extract_pyproject_deps(text)
            if pkgs:
                deps["pyproject.toml"] = pkgs
                for p in pkgs:
                    key = p.lower().split("[")[0].strip()
                    if key in PY_FRAMEWORK_HINTS:
                        frameworks.add(PY_FRAMEWORK_HINTS[key])
        except OSError:
            pass

    # Python — requirements.txt
    requirements = root / "requirements.txt"
    if requirements.exists():
        files.append(requirements)
        try:
            pkgs = []
            for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                pkg = re.split(r"[=<>~!\[]", line, 1)[0].strip()
                if pkg:
                    pkgs.append(pkg)
            if pkgs:
                deps["requirements.txt"] = pkgs
                for p in pkgs:
                    if p.lower() in PY_FRAMEWORK_HINTS:
                        frameworks.add(PY_FRAMEWORK_HINTS[p.lower()])
        except OSError:
            pass

    # Node — package.json
    pkg_json = root / "package.json"
    if pkg_json.exists():
        files.append(pkg_json)
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
            pkgs = list((data.get("dependencies") or {}).keys()) + list(
                (data.get("devDependencies") or {}).keys()
            )
            if pkgs:
                deps["package.json"] = pkgs
                for p in pkgs:
                    if p in JS_FRAMEWORK_HINTS:
                        frameworks.add(JS_FRAMEWORK_HINTS[p])
        except (OSError, json.JSONDecodeError):
            pass

    # Rust — Cargo.toml
    cargo = root / "Cargo.toml"
    if cargo.exists():
        files.append(cargo)
        try:
            text = cargo.read_text(encoding="utf-8", errors="ignore")
            pkgs = re.findall(r"^([a-zA-Z0-9_-]+)\s*=", text, flags=re.MULTILINE)
            if pkgs:
                deps["Cargo.toml"] = pkgs[:30]
        except OSError:
            pass

    # Go — go.mod
    go_mod = root / "go.mod"
    if go_mod.exists():
        files.append(go_mod)
        try:
            text = go_mod.read_text(encoding="utf-8", errors="ignore")
            pkgs = re.findall(r"require\s+([^\s]+)", text)
            if pkgs:
                deps["go.mod"] = pkgs[:30]
        except OSError:
            pass

    return sorted(frameworks), deps, files


def _extract_pyproject_deps(text: str) -> list[str]:
    """Tiny TOML-ish extractor — pulls `dependencies = [...]` and `[project.optional-dependencies]`.

    Avoids a tomllib import to keep py3.10 compat clean and side-step the
    standard library's pickiness with malformed files.
    """
    pkgs: list[str] = []
    m = re.search(r"(?ms)^dependencies\s*=\s*\[(.*?)\]", text)
    if m:
        for entry in re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1)):
            raw = entry[0] or entry[1]
            pkg = re.split(r"[=<>~!\[;\s]", raw, 1)[0].strip()
            if pkg:
                pkgs.append(pkg)
    return pkgs


def detect_entry_points(root: Path, files: list[Path], primary: Optional[str]) -> list[Path]:
    """Best-effort entry-point discovery — language-aware."""
    found: list[Path] = []

    if primary == "Python":
        for f in files:
            if f.suffix != ".py":
                continue
            name = f.name
            if name in ("__main__.py", "main.py", "app.py", "cli.py", "server.py", "manage.py"):
                found.append(f)
                continue
            try:
                head = f.read_text(encoding="utf-8", errors="ignore")[:4000]
                if "__main__" in head and "if __name__" in head:
                    found.append(f)
            except OSError:
                pass

    elif primary in ("TypeScript", "JavaScript"):
        pkg = root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
                for key in ("main", "module"):
                    if key in data:
                        candidate = root / data[key]
                        if candidate.exists():
                            found.append(candidate)
                bin_field = data.get("bin")
                if isinstance(bin_field, dict):
                    for path in bin_field.values():
                        p = root / path
                        if p.exists():
                            found.append(p)
                elif isinstance(bin_field, str):
                    p = root / bin_field
                    if p.exists():
                        found.append(p)
            except (OSError, json.JSONDecodeError):
                pass

    elif primary == "Go":
        for candidate in [root / "main.go", root / "cmd"]:
            if candidate.is_file():
                found.append(candidate)
            elif candidate.is_dir():
                for sub in candidate.iterdir():
                    main = sub / "main.go" if sub.is_dir() else None
                    if main and main.exists():
                        found.append(main)

    elif primary == "Rust":
        for candidate in [root / "src" / "main.rs", root / "src" / "lib.rs"]:
            if candidate.exists():
                found.append(candidate)
        bin_dir = root / "src" / "bin"
        if bin_dir.is_dir():
            found.extend(bin_dir.glob("*.rs"))

    # De-dupe while preserving order
    seen: set[Path] = set()
    ordered: list[Path] = []
    for f in found:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered[:10]


def detect_top_level_modules(root: Path) -> list[str]:
    """Top-level source dirs / packages — what a reader looks at first."""
    modules: list[str] = []

    # Python: package dirs under src/ or directly under root
    src = root / "src"
    if src.is_dir():
        for child in src.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                modules.append(f"src/{child.name}")

    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in {
            "tests", "test", "docs", "examples", "scripts", "node_modules",
            "venv", ".venv", "dist", "build", "src", "vendor",
        }:
            continue
        # Python package
        if (child / "__init__.py").exists():
            modules.append(child.name)
        # JS/TS source root
        elif any((child / f).exists() for f in ("index.ts", "index.js", "index.tsx")):
            modules.append(child.name)
        # Go cmd
        elif child.name == "cmd":
            modules.append("cmd")
        # Rust crate
        elif (child / "Cargo.toml").exists():
            modules.append(child.name)

    # De-dupe, sort
    return sorted(set(modules))


def detect_license(root: Path) -> tuple[Optional[str], Optional[Path]]:
    """Detect license by reading LICENSE*. Returns (label, file) or (None, None)."""
    for candidate in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md"):
        path = root / candidate
        if path.exists():
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:2000].upper()
                if "MIT LICENSE" in head or "PERMISSION IS HEREBY GRANTED, FREE OF CHARGE" in head:
                    return "MIT", path
                if "APACHE LICENSE" in head:
                    return "Apache-2.0", path
                if "GNU GENERAL PUBLIC LICENSE" in head:
                    if "VERSION 3" in head:
                        return "GPL-3.0", path
                    return "GPL", path
                if "GNU LESSER GENERAL PUBLIC LICENSE" in head:
                    return "LGPL", path
                if "MOZILLA PUBLIC LICENSE" in head:
                    return "MPL-2.0", path
                if "BSD" in head:
                    return "BSD", path
                return "Unknown", path
            except OSError:
                return None, path
    return None, None


_TEST_PATH_PARTS = frozenset({"tests", "test", "__tests__", "spec"})


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in _TEST_PATH_PARTS for p in parts):
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith(("_test.py", "_test.go", ".test.ts", ".test.js"))


def detect_public_api(root: Path, files: list[Path], primary: Optional[str]) -> list[str]:
    """Best-effort public symbol list — used later by Critic for coverage scoring.

    Excludes test files (they're not public API) and underscore-prefixed names.
    """
    symbols: list[str] = []
    if primary == "Python":
        for f in files:
            if f.suffix != ".py":
                continue
            if f.name.startswith("_") and f.name != "__init__.py":
                continue
            rel = str(f.relative_to(root)).replace("\\", "/")
            if _is_test_path(rel):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in re.finditer(r"^(?:async\s+)?def\s+([a-zA-Z][a-zA-Z0-9_]*)\s*\(", text, re.MULTILINE):
                symbols.append(f"{rel}::{m.group(1)}")
            for m in re.finditer(r"^class\s+([A-Z][a-zA-Z0-9_]*)", text, re.MULTILINE):
                symbols.append(f"{rel}::{m.group(1)}")
    return symbols[:200]
