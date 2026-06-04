"""APIScanner — extracts HTTP routes and CLI commands.

Static regex scan over source files. Recognises:
  * FastAPI/Flask/Django: `@app.get("/path")`, `@router.post("/x")`, `@app.route("/y", methods=["POST"])`
  * Express/Fastify/Koa: `app.get('/path', handler)`, `router.post('/x', ...)`
  * Click/Typer: `@click.command()` / `@app.command()` / `@cli.command("name")`
  * argparse subparsers (best-effort): `subparsers.add_parser("name", ...)`

Output: list[APIRoute] suitable for Writer to drop into API.md as a real route
table with file:line citations.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..scout.walk import walk_repo
from .state import APIRoute, GraphState


# ---- Python regexes -----------------------------------------------------

# @app.get("/path"), @router.post("/x"), @app.delete('/y')
_PY_FASTAPI = re.compile(
    r"@(?P<obj>[A-Za-z_][A-Za-z0-9_]*)\.(?P<method>get|post|put|patch|delete|options|head)"
    r"\(\s*[\"'](?P<path>[^\"']+)[\"']",
    re.IGNORECASE,
)

# @app.route("/x", methods=["POST"]) — Flask classic
_PY_FLASK_ROUTE = re.compile(
    r"@(?P<obj>[A-Za-z_][A-Za-z0-9_]*)\.route\(\s*[\"'](?P<path>[^\"']+)[\"']"
    r"(?:[^)]*methods\s*=\s*\[(?P<methods>[^\]]+)\])?",
    re.IGNORECASE,
)

# @click.command() / @app.command(...) / @cli.command("name")
_PY_CLI = re.compile(
    r"@(?P<obj>[A-Za-z_][A-Za-z0-9_]*)\.command\(\s*(?:[\"'](?P<name>[^\"']*)[\"'])?",
)

# argparse subparsers
_PY_ARGPARSE_SUB = re.compile(
    r"add_parser\(\s*[\"'](?P<name>[^\"']+)[\"']",
)

# Catches the next `def name(...)` after a decorator (handler name).
_PY_DEF_AFTER = re.compile(r"(?:^|\n)\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)")


# ---- JS/TS regexes ------------------------------------------------------

# app.get('/x', handler), router.post('/y', handler) — Express-family.
_JS_HTTP = re.compile(
    r"\b(?P<obj>app|router)\.(?P<method>get|post|put|patch|delete|options|head|all)\s*\(\s*"
    r"[\"'](?P<path>[^\"']+)[\"']",
    re.IGNORECASE,
)


# ---- Helpers ------------------------------------------------------------


def _line_no(text: str, idx: int) -> int:
    return text[:idx].count("\n") + 1


def _find_handler_after(text: str, after_idx: int) -> str:
    """Find the function name defined immediately after `after_idx`."""
    m = _PY_DEF_AFTER.search(text, pos=after_idx)
    return m.group(1) if m else ""


def _scan_python(rel: str, text: str) -> list[APIRoute]:
    routes: list[APIRoute] = []

    for m in _PY_FASTAPI.finditer(text):
        handler = _find_handler_after(text, m.end())
        routes.append(APIRoute(
            kind="http",
            method=m.group("method").upper(),
            path=m.group("path"),
            handler=handler,
            citation=f"{rel}:{_line_no(text, m.start())}",
        ))

    for m in _PY_FLASK_ROUTE.finditer(text):
        methods_raw = m.group("methods") or "GET"
        methods = [s.strip().strip("\"' ") for s in methods_raw.split(",")] or ["GET"]
        handler = _find_handler_after(text, m.end())
        for method in methods:
            routes.append(APIRoute(
                kind="http",
                method=method.upper(),
                path=m.group("path"),
                handler=handler,
                citation=f"{rel}:{_line_no(text, m.start())}",
            ))

    for m in _PY_CLI.finditer(text):
        handler = _find_handler_after(text, m.end())
        name = m.group("name") or handler or ""
        routes.append(APIRoute(
            kind="cli",
            method="CMD",
            path=name,
            handler=handler,
            citation=f"{rel}:{_line_no(text, m.start())}",
        ))

    for m in _PY_ARGPARSE_SUB.finditer(text):
        routes.append(APIRoute(
            kind="cli",
            method="CMD",
            path=m.group("name"),
            handler="",
            citation=f"{rel}:{_line_no(text, m.start())}",
        ))

    return routes


def _scan_js(rel: str, text: str) -> list[APIRoute]:
    routes: list[APIRoute] = []
    for m in _JS_HTTP.finditer(text):
        routes.append(APIRoute(
            kind="http",
            method=m.group("method").upper(),
            path=m.group("path"),
            handler="",  # JS handlers are often inline; leave empty
            citation=f"{rel}:{_line_no(text, m.start())}",
        ))
    return routes


_SUPPORTED_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def run_api_scanner(state: GraphState) -> dict:
    manifest = state["manifest"]
    repo_root = Path(manifest.repo_path)
    files, _ = walk_repo(repo_root)

    routes: list[APIRoute] = []
    for p in files:
        if p.suffix.lower() not in _SUPPORTED_EXTS:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(repo_root)).replace("\\", "/")
        if p.suffix.lower() == ".py":
            routes.extend(_scan_python(rel, text))
        else:
            routes.extend(_scan_js(rel, text))

    # De-dupe identical (kind, method, path) entries — these arise when a route is
    # registered in multiple files or a regex matches both a decorator and its retry.
    seen: set[tuple[str, str, str]] = set()
    unique: list[APIRoute] = []
    for r in routes:
        key = (r["kind"], r["method"], r["path"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    return {"api_routes": unique[:120]}  # cap to keep state and prompts modest
