"""FastAPI app for docforge — landing page, run-a-repo, results, scoreboard, showcase."""

from __future__ import annotations

import json
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

# Eval scoreboard path (relative to CWD when serving).
# Override via env in deploys if needed.
SCOREBOARD_PATH = Path("eval/scoreboard_data.json")


def create_app(registry: Optional[JobRegistry] = None) -> FastAPI:
    reg = registry or default_registry

    app = FastAPI(title="docforge", docs_url="/api/docs", redoc_url=None)
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ---- HTML pages -----------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return TEMPLATES.TemplateResponse(request, "index.html", {})

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
        return TEMPLATES.TemplateResponse(request, "showcase.html", {"scoreboard": data})

    # ---- JSON / SSE APIs ------------------------------------------------

    @app.post("/api/run")
    async def start_run(payload: StartPayload):
        if not payload.git_url and not payload.repo_path:
            raise HTTPException(400, "must provide git_url or repo_path")
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

    @app.get("/api/scoreboard")
    async def scoreboard_json():
        return JSONResponse(_load_scoreboard() or {})

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "jobs": len(reg.all())}

    return app


def _load_scoreboard() -> Optional[dict]:
    if not SCOREBOARD_PATH.is_file():
        return None
    try:
        return json.loads(SCOREBOARD_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# Default app instance for `uvicorn docforge.server.app:app`
app = create_app()
