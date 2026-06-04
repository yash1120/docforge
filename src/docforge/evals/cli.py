"""`docforge-eval` — run the full eval against the testset and write a scoreboard."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .runner import aggregate, score_repo, write_scoreboard
from .testset import load_testset

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

console = Console(legacy_windows=False, force_terminal=True, file=sys.stdout)

app = typer.Typer(add_completion=False, help="Run the docforge eval harness over a testset.")


@app.command()
def main(
    testset: Path = typer.Option(Path("eval/testset"), "--testset", help="Root of the testset directory."),
    out: Path = typer.Option(Path("eval/scoreboard_data.json"), "--out", help="Where to write the scoreboard JSON."),
    only: str = typer.Option("", "--only", help="Comma-separated list of repo names to include."),
) -> None:
    entries = load_testset(testset)
    if only:
        wanted = {x.strip() for x in only.split(",") if x.strip()}
        entries = [e for e in entries if e.name in wanted]

    if not entries:
        console.print(f"[yellow]no testset entries under {testset}[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]eval[/bold cyan] {len(entries)} repo(s): {[e.name for e in entries]}")

    results = []
    for entry in entries:
        console.print(f"\n[bold]>>[/bold] {entry.name}  ({entry.primary_language}, {len(entry.claims)} claims)")
        try:
            result = score_repo(entry)
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]failed: {type(e).__name__}: {e}[/red]")
            continue
        results.append(result)
        _print_scorecard(result)

    scoreboard = aggregate(results)
    write_scoreboard(scoreboard, out)
    console.print(f"\n[green]wrote[/green] {out}")
    _print_summary(scoreboard)


def _print_scorecard(result) -> None:
    s = result.scorecard
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("factuality",       f"{s.factuality:.2%}")
    t.add_row("coverage",         f"{s.coverage:.2%}")
    t.add_row("completeness",     f"{s.completeness:.2%}")
    t.add_row("citation density", f"{s.citation_density:.2f} per 100 words")
    t.add_row("readability",      f"{s.readability:.2f} / 5")
    t.add_row("duration",         f"{result.duration_sec:.1f}s")
    console.print(t)


def _print_summary(scoreboard: dict) -> None:
    s = scoreboard.get("summary", {})
    if not s:
        return
    console.print("\n[bold]aggregate (mean across repos)[/bold]")
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("factuality",   f"{s.get('factuality_mean', 0):.2%}")
    t.add_row("coverage",     f"{s.get('coverage_mean', 0):.2%}")
    t.add_row("completeness", f"{s.get('completeness_mean', 0):.2%}")
    t.add_row("readability",  f"{s.get('readability_mean', 0):.2f} / 5")
    console.print(t)


if __name__ == "__main__":
    app()
