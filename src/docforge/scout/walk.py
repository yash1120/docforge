from __future__ import annotations

from pathlib import Path

SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    ".venv", "venv", "env", ".env",
    "node_modules", "bower_components",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    "dist", "build", "out", "target",
    ".next", ".nuxt", ".svelte-kit", ".cache", ".parcel-cache",
    ".docforge", ".docforge_runs",
    ".idea", ".vscode", ".vs",
    "coverage", "htmlcov",
    "vendor",
})

SKIP_EXTS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".dylib", ".o", ".a",
    ".class", ".jar", ".war",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp", ".tiff",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".webm",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".sqlite", ".sqlite3", ".db",
    ".bin", ".dat", ".pkl", ".npy", ".npz",
})

MAX_FILE_BYTES = 1_500_000  # 1.5 MB — skip giant generated files


def walk_repo(root: Path) -> tuple[list[Path], int]:
    """Walk the repo, returning (kept_files, skipped_count).

    Skips common junk dirs (venvs, build artifacts, node_modules), binary
    extensions, and files > 1.5 MB. Honors a small set of .gitignore-style
    defaults so docforge produces stable output on any repo.
    """
    files: list[Path] = []
    skipped = 0

    for path in _walk(root):
        try:
            size = path.stat().st_size
        except OSError:
            skipped += 1
            continue

        if size > MAX_FILE_BYTES:
            skipped += 1
            continue

        if path.suffix.lower() in SKIP_EXTS:
            skipped += 1
            continue

        files.append(path)

    return files, skipped


def _walk(root: Path):
    """Depth-first walk yielding files, pruning SKIP_DIRS."""
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue

        for entry in entries:
            if entry.is_dir():
                if entry.name in SKIP_DIRS or entry.name.startswith("."):
                    # Drop hidden + skip-listed dirs, but keep .github (we need workflows)
                    if entry.name == ".github":
                        stack.append(entry)
                    continue
                stack.append(entry)
            elif entry.is_file():
                yield entry
