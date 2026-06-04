"""ConfigReader — env vars + config files.

Static scan for:
  * `os.environ.get("X", default)` / `os.environ["X"]` / `os.getenv("X")`
  * `process.env.X` / `process.env["X"]`
  * `.env`, `.env.example`, `settings.toml`, `config.yaml`, etc.

Writer uses this to build a real "Setup" section in TUTORIAL.md ("Set
DATABASE_URL and OPENAI_API_KEY before running") and a real Configuration
table in README.md.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..scout.walk import walk_repo
from .state import ConfigSummary, EnvVar, GraphState


# Python: os.environ.get("X", default), os.environ["X"], os.getenv("X", default)
_PY_ENV_GET = re.compile(
    r"""os\.(?:environ\.get|getenv)\(\s*[\"']([A-Z_][A-Z0-9_]*)[\"']"""
    r"""(?:\s*,\s*(?P<default>[^\)]+?))?\)""",
)
_PY_ENV_INDEX = re.compile(r"""os\.environ\[\s*[\"']([A-Z_][A-Z0-9_]*)[\"']\s*\]""")

# JS/TS: process.env.X or process.env["X"]
_JS_ENV_DOT = re.compile(r"""process\.env\.([A-Z_][A-Z0-9_]*)""")
_JS_ENV_INDEX = re.compile(r"""process\.env\[\s*[\"']([A-Z_][A-Z0-9_]*)[\"']\s*\]""")

# .env / .env.example lines like `FOO=bar`
_ENV_FILE_LINE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)$", re.MULTILINE)

# Common config filenames we surface verbatim.
_CONFIG_FILENAMES = frozenset({
    ".env", ".env.example", ".env.local", ".env.development", ".env.production",
    "settings.toml", "settings.yaml", "settings.yml", "settings.json",
    "config.toml", "config.yaml", "config.yml", "config.json",
    "app.toml", "appsettings.json",
})


def _line_no(text: str, idx: int) -> int:
    return text[:idx].count("\n") + 1


# Variables whose values are almost certainly sensitive — we never persist
# the raw value to disk (or expose it in prompts). The presence is what matters
# for the Writer; the value belongs in the user's .env, not our state.
_SECRET_HINT = re.compile(
    r"(?:API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE|CREDENTIAL|AUTH|DSN|CONNECTION_STRING)",
    re.IGNORECASE,
)


def _is_secret_name(name: str) -> bool:
    return bool(_SECRET_HINT.search(name))


def _clean_default(raw: str | None) -> str | None:
    if raw is None:
        return None
    raw = raw.strip()
    # Strip surrounding quotes
    if (raw.startswith("\"") and raw.endswith("\"")) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    if raw in ("None", "null", ""):
        return None
    return raw[:80]


def _safe_default(name: str, raw: str | None) -> str | None:
    """Same as _clean_default but redacts secret-looking values to a sentinel string."""
    val = _clean_default(raw)
    if val is None:
        return None
    if _is_secret_name(name):
        return "<redacted>"
    return val


def _scan_python(rel: str, text: str, found: dict[str, EnvVar]) -> None:
    for m in _PY_ENV_GET.finditer(text):
        name = m.group(1)
        default = _safe_default(name, m.group("default"))
        # Required if there's no default (just os.environ["X"] form) — first match wins.
        ev = found.get(name) or EnvVar(name=name, default=default, required=(default is None),
                                       citation=f"{rel}:{_line_no(text, m.start())}")
        # If this is a more informative occurrence, update.
        if default is not None and ev["default"] is None:
            ev["default"] = default
            ev["required"] = False
        found[name] = ev

    for m in _PY_ENV_INDEX.finditer(text):
        name = m.group(1)
        if name in found:
            continue
        found[name] = EnvVar(
            name=name, default=None, required=True,
            citation=f"{rel}:{_line_no(text, m.start())}",
        )


def _scan_js(rel: str, text: str, found: dict[str, EnvVar]) -> None:
    for regex in (_JS_ENV_DOT, _JS_ENV_INDEX):
        for m in regex.finditer(text):
            name = m.group(1)
            if name in found:
                continue
            found[name] = EnvVar(
                name=name, default=None, required=True,
                citation=f"{rel}:{_line_no(text, m.start())}",
            )


def _scan_env_file(rel: str, text: str, found: dict[str, EnvVar]) -> None:
    """Parse a .env-style file. Values found here mark the var as 'known to need a default'.

    Real `.env` files (vs `.env.example`) often hold live secrets. We redact
    secret-looking names before they ever land in state.
    """
    is_real_env = rel.endswith("/.env") or rel == ".env" or rel.endswith("\\.env")
    for m in _ENV_FILE_LINE.finditer(text):
        name = m.group(1)
        raw = m.group(2).strip().strip("\"'") or None
        if _is_secret_name(name) or is_real_env:
            # For `.env` (not .env.example), assume every populated value is private.
            default = "<redacted>" if raw else None
        else:
            default = raw

        if name in found:
            # Improve the entry with a default if we have one.
            if default and not found[name]["default"]:
                found[name]["default"] = default
                found[name]["required"] = False
            continue
        found[name] = EnvVar(
            name=name, default=default, required=(default is None),
            citation=f"{rel}:{_line_no(text, m.start())}",
        )


def run_config_reader(state: GraphState) -> dict:
    manifest = state["manifest"]
    repo_root = Path(manifest.repo_path)
    files, _ = walk_repo(repo_root)

    found: dict[str, EnvVar] = {}
    config_files: list[str] = []

    for p in files:
        rel = str(p.relative_to(repo_root)).replace("\\", "/")
        name = p.name.lower()
        ext = p.suffix.lower()

        # Track config files we recognize
        if name in _CONFIG_FILENAMES:
            config_files.append(rel)

        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if ext == ".py":
            _scan_python(rel, text, found)
        elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            _scan_js(rel, text, found)

        # .env-style files (could be in /test fixtures too — that's fine, still informative)
        if name in {".env", ".env.example", ".env.local",
                    ".env.development", ".env.production"}:
            _scan_env_file(rel, text, found)

    summary = ConfigSummary(
        env_vars=list(found.values()),
        config_files=sorted(set(config_files)),
    )
    return {"config_summary": summary}
