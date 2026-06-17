"""`docforge-serve` — launches uvicorn in front of the FastAPI app."""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="Run the docforge web server.")


@app.command()
def main(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host."),
    port: int = typer.Option(8000, "--port", help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev)."),
) -> None:
    import uvicorn
    uvicorn.run(
        "docforge.server.app:app",
        host=host, port=port, reload=reload, log_level="info",
    )


if __name__ == "__main__":
    app()
