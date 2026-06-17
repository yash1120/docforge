"""Async job runner — drives the docforge pipeline in a background task and
exposes a per-job event queue the SSE endpoint consumes.

The webserver hands a `JobRequest` to `JobRegistry.create_job`; the registry
spins up a thread, runs `scout → index → graph.stream(...)`, and pushes
typed `Event` dicts to a queue per agent transition. The job ends with a
final `done` or `error` event so the SSE consumer can close cleanly.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any, Optional

from ..agents import build_graph, initial_state
from ..indexer import build_index
from ..scout import build_manifest
from ..scout.walk import walk_repo


@dataclass
class JobRequest:
    repo_path: Optional[str] = None
    git_url: Optional[str] = None
    skip_index: bool = False


@dataclass
class JobState:
    id: str
    status: str = "queued"              # queued | running | done | error
    repo_name: str = ""
    repo_source: str = ""
    started_at: float = 0.0
    duration_sec: float = 0.0
    error: str = ""
    drafts: dict[str, str] = field(default_factory=dict)
    diagram_mmd: str = ""
    architecture: dict = field(default_factory=dict)
    module_summaries: list = field(default_factory=list)
    test_summary: dict = field(default_factory=dict)
    api_routes: list = field(default_factory=list)
    config_summary: dict = field(default_factory=dict)
    critique: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    def to_summary(self) -> dict:
        """Lightweight JSON for the status endpoint (no doc bodies)."""
        return {
            "id": self.id,
            "status": self.status,
            "repo_name": self.repo_name,
            "repo_source": self.repo_source,
            "duration_sec": round(self.duration_sec, 1),
            "error": self.error,
            "doc_names": list(self.drafts.keys()),
            "events": self.events,
        }


class JobRegistry:
    """Thread-safe registry of in-flight + completed jobs.

    Memory only — fine for the demo. Production would persist to sqlite/redis.
    Old jobs are GC'd by count: we keep the most recent MAX_JOBS.
    """

    MAX_JOBS = 100

    def __init__(self):
        self._jobs: dict[str, JobState] = {}
        self._queues: dict[str, Queue] = {}
        self._lock = threading.RLock()

    def get(self, job_id: str) -> Optional[JobState]:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[JobState]:
        with self._lock:
            return list(self._jobs.values())

    def create_job(self, req: JobRequest) -> JobState:
        job_id = uuid.uuid4().hex[:12]
        state = JobState(id=job_id, status="queued", started_at=time.time())
        with self._lock:
            self._jobs[job_id] = state
            self._queues[job_id] = Queue()
            # GC older jobs
            if len(self._jobs) > self.MAX_JOBS:
                oldest = sorted(self._jobs.values(), key=lambda j: j.started_at)[:len(self._jobs) - self.MAX_JOBS]
                for j in oldest:
                    self._jobs.pop(j.id, None)
                    self._queues.pop(j.id, None)
        threading.Thread(target=self._run, args=(state, req), daemon=True).start()
        return state

    async def events(self, job_id: str):
        """Async iterator over the job's event queue. Closes when the queue
        receives a sentinel `None`."""
        q = self._queues.get(job_id)
        if q is None:
            return
        loop = asyncio.get_event_loop()
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                return
            yield event

    # ---- internals ------------------------------------------------------

    def _emit(self, state: JobState, event: dict) -> None:
        event = {**event, "at": round(time.time() - state.started_at, 2)}
        state.events.append(event)
        q = self._queues.get(state.id)
        if q is not None:
            q.put(event)

    def _close(self, state: JobState) -> None:
        q = self._queues.get(state.id)
        if q is not None:
            q.put(None)

    def _run(self, state: JobState, req: JobRequest) -> None:
        try:
            repo = self._resolve(req)
            state.repo_source = req.git_url or req.repo_path or ""
            state.repo_name = repo.name
            state.status = "running"
            self._emit(state, {"agent": "system", "kind": "start",
                               "message": f"resolved repo {repo}"})

            # ---- Scout ----
            self._emit(state, {"agent": "scout", "kind": "begin"})
            manifest = build_manifest(repo)
            files, _ = walk_repo(repo)
            self._emit(state, {"agent": "scout", "kind": "end",
                               "stats": {"code_files": manifest.total_code_files,
                                         "loc": manifest.total_loc,
                                         "primary": manifest.primary_language}})

            # ---- Index ----
            persist = repo / ".docforge" / "chroma"
            self._emit(state, {"agent": "indexer", "kind": "begin"})
            index, idx_stats = build_index(repo, files, manifest.repo_name, persist_dir=persist)
            self._emit(state, {"agent": "indexer", "kind": "end",
                               "stats": {"chunks": idx_stats.chunks,
                                         "files_chunked": idx_stats.files_chunked}})

            # ---- Graph (with streamed per-node events) ----
            graph = build_graph(index, critic_loop=True)
            init = initial_state(str(repo), manifest.repo_name, manifest, str(repo / ".docforge"))

            # LangGraph .stream yields {node_name: state_update} per node completion.
            final: dict[str, Any] = dict(init)
            for chunk in graph.stream(init):
                for node_name, _ in chunk.items():
                    self._emit(state, {"agent": node_name, "kind": "node_done"})
                # Merge the partial update into our running snapshot
                for _, partial in chunk.items():
                    if isinstance(partial, dict):
                        final.update(partial)

            # ---- Copy out into state ----
            state.drafts = final.get("drafts", {}) or {}
            state.diagram_mmd = final.get("diagram_mmd", "") or ""
            state.architecture = final.get("architecture", {}) or {}
            state.module_summaries = final.get("module_summaries", []) or []
            state.test_summary = final.get("test_summary", {}) or {}
            state.api_routes = final.get("api_routes", []) or []
            state.config_summary = final.get("config_summary", {}) or {}
            state.critique = final.get("critique", {}) or {}

            state.duration_sec = time.time() - state.started_at
            state.status = "done"
            self._emit(state, {"agent": "system", "kind": "done"})
        except Exception as e:  # noqa: BLE001 — runner is the safety net
            state.error = f"{type(e).__name__}: {e}"
            state.status = "error"
            self._emit(state, {"agent": "system", "kind": "error", "message": state.error})
        finally:
            self._close(state)

    def _resolve(self, req: JobRequest) -> Path:
        if req.repo_path:
            p = Path(req.repo_path)
            if not p.is_dir():
                raise FileNotFoundError(f"repo_path missing: {p}")
            return p.resolve()
        if req.git_url:
            target = Path(tempfile.gettempdir()) / "docforge-runs" / uuid.uuid4().hex[:8]
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth", "1", req.git_url, str(target)],
                check=True, capture_output=True, timeout=180,
            )
            return target
        raise ValueError("must specify repo_path or git_url")


# Module-level default registry — the FastAPI app uses this. Tests inject their own.
default_registry = JobRegistry()
