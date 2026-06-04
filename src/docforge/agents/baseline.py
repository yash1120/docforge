"""Single-agent baseline. Feed manifest + a handful of files to one LLM, get a README.

This is the shame line — every multi-agent improvement in Week 2+ must beat it
on the eval. We keep it deliberately simple: no critic loop, no chunking strategy,
no fancy retrieval. Just `LLM(repo_facts) -> markdown`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..llm import Message, chat
from ..scout import Manifest


BASELINE_SYSTEM = """You are a senior engineer writing a README for a repository you just inherited.

Rules:
1. Be honest about what you can see — if the codebase doesn't show evidence of something, do NOT invent it.
2. Cite files inline as `[path/to/file.py:L42]` whenever you make a claim about behavior. Citations let readers verify.
3. Structure: short pitch (2-3 sentences) → Install/Run → Architecture (one paragraph) → Key Modules (bullet list with line citations) → License.
4. No marketing fluff. No emoji. No "this innovative project". Plain technical prose.
5. If the user already provides an existing README, treat it as input, not gospel — correct anything the code contradicts."""


BASELINE_USER_TEMPLATE = """# Repo: {repo_name}

## Manifest (from static scout)
```json
{manifest_json}
```

## Existing README (if any)
{existing_readme}

## Selected source files
{file_contents}

---

Write the new README.md. Output ONLY the markdown — no commentary before or after."""


# Hard caps so we don't blow the 8k/32k context windows on free-tier Groq.
MAX_FILE_BYTES_EACH = 6000
MAX_TOTAL_FILE_BYTES = 40_000
MAX_FILES = 10


@dataclass
class BaselineResult:
    markdown: str
    files_included: list[str]
    prompt_chars: int


def select_files(manifest: Manifest) -> list[Path]:
    """Pick the most informative files for a one-shot prompt.

    Priority: entry points → top-of-each-top-level-module → dependency files.
    """
    repo_root = Path(manifest.repo_path)
    picked: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        if p in seen or not p.exists() or not p.is_file():
            return
        picked.append(p)
        seen.add(p)

    # 1. Entry points
    for rel in manifest.entry_points:
        add(repo_root / rel)

    # 2. First "interesting" file per top-level module (Python: __init__ then main-ish)
    for mod in manifest.top_level_modules:
        mod_path = repo_root / mod
        if not mod_path.is_dir():
            continue
        for candidate in ("__init__.py", "main.py", "app.py", "cli.py", "index.ts", "index.js"):
            add(mod_path / candidate)

    # 3. Top dependency manifest files (for stack context)
    for rel in manifest.dependency_files[:2]:
        add(repo_root / rel)

    return picked[:MAX_FILES]


def render_file_block(repo_root: Path, path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if len(text) > MAX_FILE_BYTES_EACH:
        text = text[:MAX_FILE_BYTES_EACH] + "\n# ...truncated for prompt size\n"
    rel = str(path.relative_to(repo_root)).replace("\\", "/")
    return f"### `{rel}`\n```{_lang_fence(path.suffix)}\n{text}\n```\n"


def _lang_fence(ext: str) -> str:
    return {
        ".py": "python", ".ts": "typescript", ".tsx": "tsx",
        ".js": "javascript", ".jsx": "jsx",
        ".go": "go", ".rs": "rust", ".java": "java",
        ".rb": "ruby", ".sh": "bash", ".sql": "sql",
        ".yaml": "yaml", ".yml": "yaml", ".json": "json",
        ".toml": "toml", ".md": "markdown",
    }.get(ext.lower(), "")


def run_baseline(manifest: Manifest, *, temperature: float = 0.2) -> BaselineResult:
    repo_root = Path(manifest.repo_path)
    files = select_files(manifest)

    # Pack file blocks, stop when budget is hit.
    blocks: list[str] = []
    included: list[str] = []
    total = 0
    for f in files:
        block = render_file_block(repo_root, f)
        if not block:
            continue
        if total + len(block) > MAX_TOTAL_FILE_BYTES and included:
            break
        blocks.append(block)
        included.append(str(f.relative_to(repo_root)).replace("\\", "/"))
        total += len(block)

    # Existing README, if any
    existing = ""
    if manifest.readme_path:
        readme_path = repo_root / manifest.readme_path
        try:
            existing = readme_path.read_text(encoding="utf-8", errors="ignore")
            if len(existing) > 6000:
                existing = existing[:6000] + "\n# ...truncated\n"
        except OSError:
            pass
    if not existing.strip():
        existing = "_(no existing README in repo)_"

    manifest_compact = manifest.model_dump()
    # Trim the public_api list — full list bloats the prompt and isn't read.
    manifest_compact["public_api"] = manifest_compact["public_api"][:20]
    manifest_json = json.dumps(manifest_compact, indent=2)

    user_msg = BASELINE_USER_TEMPLATE.format(
        repo_name=manifest.repo_name,
        manifest_json=manifest_json,
        existing_readme=existing,
        file_contents="\n".join(blocks),
    )

    text = chat(
        [
            Message(role="system", content=BASELINE_SYSTEM),
            Message(role="user", content=user_msg),
        ],
        temperature=temperature,
        max_tokens=3000,
    )

    return BaselineResult(
        markdown=text.strip(),
        files_included=included,
        prompt_chars=len(BASELINE_SYSTEM) + len(user_msg),
    )
