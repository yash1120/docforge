"""Web server tests — endpoint shapes, page rendering, error paths.

We inject a stub JobRegistry so no real docforge pipeline is invoked. The full
pipeline is exercised by the supervisor end-to-end test in `test_parallel_scouts.py`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from queue import Queue

import pytest
from fastapi.testclient import TestClient

from docforge.server import create_app
from docforge.server.jobs import JobRegistry, JobRequest, JobState


# ---- Stub registry ------------------------------------------------------


class StubRegistry(JobRegistry):
    """JobRegistry that doesn't actually run the docforge pipeline.

    `create_job` synthesizes a JobState we control via `next_outcome`.
    """

    def __init__(self):
        super().__init__()
        self.next_outcome: str = "done"
        self.next_drafts: dict[str, str] = {"README.md": "# stub\n"}

    def create_job(self, req: JobRequest) -> JobState:
        import uuid
        job_id = uuid.uuid4().hex[:12]
        repo_name = (
            (Path(req.repo_path).name if req.repo_path else None)
            or (req.git_url.rstrip("/").split("/")[-1] if req.git_url else "unknown")
        )
        state = JobState(
            id=job_id,
            status=self.next_outcome,
            repo_name=repo_name,
            repo_source=req.repo_path or req.git_url or "",
            started_at=time.time(),
            duration_sec=0.5,
            drafts=dict(self.next_drafts) if self.next_outcome == "done" else {},
            events=[
                {"agent": "system", "kind": "start", "at": 0.0},
                {"agent": "system", "kind": "done", "at": 0.5},
            ],
            error="boom" if self.next_outcome == "error" else "",
        )
        self._jobs[job_id] = state
        # Pre-seed the SSE queue with the two canned events then a sentinel.
        q: Queue = Queue()
        for ev in state.events:
            q.put(ev)
        q.put(None)
        self._queues[job_id] = q
        return state


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # Point the scoreboard loader at a temp file so tests can opt into having data.
    from docforge.server import app as server_app
    sb_path = tmp_path / "scoreboard_data.json"
    monkeypatch.setattr(server_app, "SCOREBOARD_PATH", sb_path)
    reg = StubRegistry()
    return TestClient(create_app(reg))


@pytest.fixture
def client_with_scoreboard(tmp_path: Path, monkeypatch) -> TestClient:
    from docforge.server import app as server_app
    sb_path = tmp_path / "scoreboard_data.json"
    sb_path.write_text(json.dumps({
        "judge": "claude-sonnet-4-6",
        "n_repos": 2,
        "summary": {
            "factuality_mean": 0.91, "coverage_mean": 0.72,
            "completeness_mean": 0.95, "citation_density_mean": 2.3,
            "readability_mean": 4.1,
        },
        "repos": [
            {"repo": "fastapi", "factuality": 0.93, "coverage": 0.75,
             "completeness": 1.0, "citation_density": 2.5, "readability": 4.2},
            {"repo": "ripgrep", "factuality": 0.89, "coverage": 0.70,
             "completeness": 0.90, "citation_density": 2.1, "readability": 4.0},
        ],
    }))
    monkeypatch.setattr(server_app, "SCOREBOARD_PATH", sb_path)
    reg = StubRegistry()
    return TestClient(create_app(reg))


# ---- Pages --------------------------------------------------------------


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "docforge" in r.text.lower()
    assert "github url" in r.text.lower()


def test_scoreboard_renders_placeholder_when_no_data(client):
    r = client.get("/scoreboard")
    assert r.status_code == 200
    assert "no" in r.text.lower() and "scoreboard_data.json" in r.text


def test_scoreboard_renders_data_when_present(client_with_scoreboard):
    r = client_with_scoreboard.get("/scoreboard")
    assert r.status_code == 200
    assert "fastapi" in r.text
    assert "93%" in r.text or "0.93" in r.text  # factuality fastapi
    assert "ripgrep" in r.text


def test_showcase_renders_placeholder_when_no_data(client):
    r = client.get("/showcase")
    assert r.status_code == 200
    assert "showcase" in r.text.lower()


def test_showcase_renders_repo_cards_when_data(client_with_scoreboard):
    r = client_with_scoreboard.get("/showcase")
    assert r.status_code == 200
    assert "fastapi" in r.text and "ripgrep" in r.text


# ---- API ----------------------------------------------------------------


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_post_run_requires_source(client):
    r = client.post("/api/run", json={})
    assert r.status_code == 400


def test_post_run_returns_id_for_git_url(client):
    r = client.post("/api/run", json={"git_url": "https://github.com/owner/repo"})
    assert r.status_code == 200
    job_id = r.json()["id"]
    assert isinstance(job_id, str) and len(job_id) >= 8


def test_post_run_returns_id_for_repo_path(client, tmp_path: Path):
    (tmp_path / "x.py").write_text("def f(): pass\n")
    r = client.post("/api/run", json={"repo_path": str(tmp_path)})
    assert r.status_code == 200
    job_id = r.json()["id"]
    assert job_id


def test_get_run_status_404_for_unknown(client):
    r = client.get("/api/run/does-not-exist")
    assert r.status_code == 404


def test_get_run_status_returns_summary(client):
    r = client.post("/api/run", json={"git_url": "https://github.com/owner/repo"})
    job_id = r.json()["id"]
    r2 = client.get(f"/api/run/{job_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == job_id
    assert body["status"] == "done"
    assert "README.md" in body["doc_names"]


def test_get_doc_returns_markdown(client):
    r = client.post("/api/run", json={"git_url": "https://github.com/owner/repo"})
    job_id = r.json()["id"]
    r2 = client.get(f"/api/run/{job_id}/doc/README.md")
    assert r2.status_code == 200
    assert "# stub" in r2.text


def test_get_doc_404_for_unknown_name(client):
    r = client.post("/api/run", json={"git_url": "https://github.com/owner/repo"})
    job_id = r.json()["id"]
    r2 = client.get(f"/api/run/{job_id}/doc/MISSING.md")
    assert r2.status_code == 404


def test_results_page_renders_for_known_job(client):
    r = client.post("/api/run", json={"git_url": "https://github.com/owner/repo"})
    job_id = r.json()["id"]
    r2 = client.get(f"/run/{job_id}")
    assert r2.status_code == 200
    assert job_id in r2.text or "README.md" in r2.text


def test_results_page_404_for_unknown(client):
    r = client.get("/run/nope")
    assert r.status_code == 404


def test_scoreboard_api_returns_json(client_with_scoreboard):
    r = client_with_scoreboard.get("/api/scoreboard")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["factuality_mean"] == 0.91
    assert len(body["repos"]) == 2


def test_scoreboard_api_returns_empty_dict_when_no_file(client):
    r = client.get("/api/scoreboard")
    assert r.status_code == 200
    assert r.json() == {}
