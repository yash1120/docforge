"""`docforge-serve` — launches uvicorn in front of the FastAPI app."""

from __future__ import annotations

import os

import typer

app = typer.Typer(add_completion=False, help="Run the docforge web server.")


def _default_port() -> int:
    """Honour the $PORT env var that Render / Railway / Heroku / Fly inject.

    Falls back to 8000 for local dev. A CLI `--port` always wins.
    """
    raw = os.environ.get("PORT", "8000")
    try:
        return int(raw)
    except ValueError:
        return 8000


@app.command()
def main(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host."),
    port: int = typer.Option(_default_port, "--port", help="Bind port (defaults to $PORT, else 8000)."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev)."),
) -> None:
    import uvicorn
    uvicorn.run(
        "docforge.server.app:app",
        host=host, port=port, reload=reload, log_level="info",
    )


if __name__ == "__main__":
    app()
