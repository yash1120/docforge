"""docforge web server.

Run with:
    uvicorn docforge.server.app:app --host 0.0.0.0 --port 8000

Or the console script registered in pyproject.toml:
    docforge-serve
"""

from .app import create_app
from .jobs import JobRegistry, JobRequest, JobState

__all__ = ["create_app", "JobRegistry", "JobRequest", "JobState"]
