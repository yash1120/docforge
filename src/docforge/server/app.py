"""FastAPI app for docforge — explainer landing, run-a-repo, results, example,
scoreboard, showcase."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .jobs import JobRegistry, JobRequest, default_registry


class StartPayload(BaseModel):
    git_url: Optional[str] = None
    repo_path: Optional[str] = None


HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))
STATIC_DIR = HERE / "static"

# Repo-root relative paths (resolved from CWD when serving; overridable via env).
SCOREBOARD_PATH = Path(os.environ.get("DOCFORGE_SCOREBOARD", "eval/scoreboard_data.json"))
EXAMPLES_DIR = Path(os.environ.get("DOCFORGE_EXAMPLES", "examples"))


def _provider() -> str:
    """Which LLM provider is configured (groq | anthropic | none)."""
    try:
        from ..llm import provider_in_use
        return provider_in_use()
    except Exception:
        if os.environ.get("GROQ_API_KEY"):
            return "groq"
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        return "none"


def create_app(registry: Optional[JobRegistry] = None) -> FastAPI:
    reg = registry or default_registry

    app = FastAPI(title="docforge", docs_url="/api/docs", redoc_url=None)
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ---- HTML pages -----------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "index.html",
            {"provider": _provider(), "has_example": _example_names()},
        )

    @app.get("/example", response_class=HTMLResponse)
    async def example_page(request: Request):
        ex = _load_example("daimon")
        if not ex:
            raise HTTPException(404, "no baked example found")
        return TEMPLATES.TemplateResponse(request, "example.html", {"ex": ex})

    @app.get("/run/{job_id}", response_class=HTMLResponse)
    async def results_page(request: Request, job_id: str):
        job = reg.get(job_id)
        if not job:
            raise HTTPException(404, f"job {job_id} not found")
        return TEMPLATES.TemplateResponse(request, "results.html", {"job": job})

    @app.get("/scoreboard", response_class=HTMLResponse)
    async def scoreboard_page(request: Request):
        data = _load_scoreboard()
        return TEMPLATES.TemplateResponse(request, "scoreboard.html", {"scoreboard": data})

    @app.get("/showcase", response_class=HTMLResponse)
    async def showcase_page(request: Request):
        data = _load_scoreboard()
        return TEMPLATES.TemplateResponse(
            request, "showcase.html",
            {"scoreboard": data, "example": _load_example("daimon")},
        )

    # ---- JSON / SSE APIs ------------------------------------------------

    @app.post("/api/run")
    async def start_run(payload: StartPayload):
        if not payload.git_url and not payload.repo_path:
            raise HTTPException(400, "must provide git_url or repo_path")
        if _provider() == "none":
            raise HTTPException(
                503,
                "No LLM provider configured on this server. The live run needs a "
                "GROQ_API_KEY (or ANTHROPIC_API_KEY). See /example for a real run.",
            )
        state = reg.create_job(JobRequest(
            repo_path=payload.repo_path, git_url=payload.git_url,
        ))
        return {"id": state.id}

    @app.get("/api/run/{job_id}")
    async def run_status(job_id: str):
        job = reg.get(job_id)
        if not job:
            raise HTTPException(404, "not found")
        return JSONResponse(job.to_summary())

    @app.get("/api/run/{job_id}/stream")
    async def run_stream(job_id: str):
        job = reg.get(job_id)
        if not job:
            raise HTTPException(404, "not found")

        async def event_gen():
            async for event in reg.events(job_id):
                yield {"event": event.get("kind", "tick"), "data": json.dumps(event)}

        return EventSourceResponse(event_gen())

    @app.get("/api/run/{job_id}/doc/{name}", response_class=PlainTextResponse)
    async def run_doc(job_id: str, name: str):
        job = reg.get(job_id)
        if not job or name not in job.drafts:
            raise HTTPException(404, "not found")
        return job.drafts[name]

    @app.get("/api/example")
    async def example_json():
        return JSONResponse(_load_example("daimon") or {})

    @app.get("/api/scoreboard")
    async def scoreboard_json():
        return JSONResponse(_load_scoreboard() or {})

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "provider": _provider(), "jobs": len(reg.all())}

    return app


def _load_scoreboard() -> Optional[dict]:
    if not SCOREBOARD_PATH.is_file():
        return None
    try:
        return json.loads(SCOREBOARD_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _example_names() -> list[str]:
    if not EXAMPLES_DIR.is_dir():
        return []
    return [p.name for p in EXAMPLES_DIR.iterdir() if p.is_dir() and (p / "run.json").exists()]


def _load_example(name: str) -> Optional[dict]:
    """Load a baked example run: run.json metadata + the generated markdown docs."""
    base = EXAMPLES_DIR / name
    run = base / "run.json"
    if not run.is_file():
        return None
    try:
        data = json.loads(run.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    docs: dict[str, str] = {}
    for md in ("README.md", "ARCHITECTURE.md", "API.md", "TUTORIAL.md"):
        p = base / md
        if p.is_file():
            docs[md] = p.read_text(encoding="utf-8", errors="ignore")
    diagram = base / "diagram.mmd"
    data["docs"] = docs
    data["diagram_mmd"] = diagram.read_text(encoding="utf-8", errors="ignore") if diagram.is_file() else ""
    return data


# Default app instance for `uvicorn docforge.server.app:app`
app = create_app()
