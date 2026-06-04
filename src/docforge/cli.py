"""docforge CLI.

Default pipeline: scout -> index -> agent team (reader/architect/diagrammer/writer)
-> docs/. Pass --baseline to additionally run the Week 1 single-agent baseline
(handy for the eval — every multi-agent improvement must beat the baseline).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .agents import (
    build_graph,
    initial_state,
    langfuse_callbacks,
    run_baseline,
)
from .indexer import CodeIndex, build_index
from .llm import LLMError, provider_in_use
from .scout import build_manifest
from .scout.walk import walk_repo

# Force UTF-8 stdout so Windows cp1252 doesn't choke on arrows.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

console = Console(legacy_windows=False, force_terminal=True, file=sys.stdout)

app = typer.Typer(
    add_completion=False,
    help="docforge — point at a repo, get an honest docs/ folder back.",
)


@app.command()
def main(
    repo: Path = typer.Argument(..., help="Path to the repo to document.", exists=True, file_okay=False),
    skip_index: bool = typer.Option(False, "--skip-index", help="Skip the embedding index (forces --no-team)."),
    no_team: bool = typer.Option(False, "--no-team", help="Skip the multi-agent team."),
    baseline: bool = typer.Option(False, "--baseline", help="Also run the Week 1 single-agent baseline alongside the team."),
    no_beautify: bool = typer.Option(False, "--no-beautify", help="Skip the LLM beautify pass in the Diagrammer (use deterministic Mermaid only)."),
    reset: bool = typer.Option(False, "--reset", help="Wipe .docforge/ before running."),
    output_dir_name: str = typer.Option(".docforge", "--out", help="Output dir name inside the target repo."),
) -> None:
    """Run the docforge pipeline against a repository."""
    repo = repo.resolve()
    out = repo / output_dir_name

    if reset and out.exists():
        shutil.rmtree(out)
        console.print(f"[dim]reset: removed {out}[/dim]")
    out.mkdir(parents=True, exist_ok=True)
    (out / "docs").mkdir(exist_ok=True)

    started = time.time()

    # ---- Scout -----------------------------------------------------------
    t0 = time.time()
    console.print(f"[bold cyan]scout[/bold cyan] walking [white]{repo}[/white] ...")
    manifest = build_manifest(repo)
    files, _ = walk_repo(repo)
    (out / "manifest.json").write_text(
        json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
    )
    console.print(
        f"  -> {manifest.total_code_files} code files, {manifest.total_loc} LOC, "
        f"primary={manifest.primary_language}  [dim]({time.time()-t0:.1f}s)[/dim]"
    )
    _print_manifest_summary(manifest)

    # ---- Index -----------------------------------------------------------
    index: CodeIndex | None = None
    if skip_index:
        console.print("\n[dim]index: skipped[/dim]")
    else:
        t1 = time.time()
        console.print("\n[bold cyan]index[/bold cyan] chunk + embed -> Chroma ...")
        chroma_dir = out / "chroma"
        index, stats = build_index(
            repo_root=repo,
            files=files,
            repo_name=manifest.repo_name,
            persist_dir=chroma_dir,
        )
        (out / "index_stats.json").write_text(
            json.dumps(
                {
                    "chunks": stats.chunks,
                    "files_chunked": stats.files_chunked,
                    "files_skipped": stats.files_skipped,
                    "duration_sec": round(time.time() - t1, 2),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        console.print(
            f"  -> {stats.chunks} chunks across {stats.files_chunked} files  "
            f"[dim]({time.time()-t1:.1f}s)[/dim]"
        )

    # ---- Provider check (both team and baseline need an LLM) -------------
    provider = provider_in_use()
    if provider == "none":
        console.print(
            "\n[yellow]baseline & team: SKIPPED — no GROQ_API_KEY or ANTHROPIC_API_KEY set.[/yellow]\n"
            "          set one in [white]docforge/.env[/white] and re-run."
        )
        return
    console.print(f"\n[dim]LLM provider: {provider}[/dim]")

    # ---- Agent team ------------------------------------------------------
    if not no_team and not skip_index and index is not None:
        _run_team(repo, out, manifest, index, beautify=not no_beautify)
    elif not no_team and (skip_index or index is None):
        console.print("[yellow]team: SKIPPED — needs the index. Drop --skip-index.[/yellow]")

    # ---- Baseline (opt-in) ----------------------------------------------
    if baseline:
        _run_baseline(repo, out, manifest)

    console.print(
        f"\n[green]done[/green] total {time.time()-started:.1f}s — outputs in [white]{out.relative_to(repo)}/[/white]"
    )


def _run_team(repo: Path, out: Path, manifest, index: CodeIndex, *, beautify: bool) -> None:
    console.print("\n[bold cyan]team[/bold cyan] reader -> architect -> diagrammer -> writer ...")
    t = time.time()
    graph = build_graph(index)
    state = initial_state(
        repo_path=str(repo),
        repo_name=manifest.repo_name,
        manifest=manifest,
        out_dir=str(out),
    )
    # Diagrammer reads `beautify` from a flag inside the node, but for now we keep
    # the toggle at CLI level via env so we don't have to thread config through.
    import os
    os.environ["DOCFORGE_DIAGRAM_BEAUTIFY"] = "1" if beautify else "0"

    config = {"callbacks": langfuse_callbacks()}
    try:
        final: dict = graph.invoke(state, config)
    except LLMError as e:
        console.print(f"[red]team failed: {e}[/red]")
        raise typer.Exit(code=1)

    # Persist intermediates so users can inspect what each agent produced.
    (out / "module_summaries.json").write_text(
        json.dumps(final.get("module_summaries", []), indent=2), encoding="utf-8"
    )
    (out / "architecture.json").write_text(
        json.dumps(final.get("architecture", {}), indent=2), encoding="utf-8"
    )
    diagram = final.get("diagram_mmd", "")
    (out / "docs" / "diagram.mmd").write_text(diagram, encoding="utf-8")

    drafts = final.get("drafts", {})
    for name, body in drafts.items():
        (out / "docs" / name).write_text(body, encoding="utf-8")

    errors = final.get("errors", [])
    if errors:
        (out / "errors.log").write_text("\n".join(errors), encoding="utf-8")

    sizes = ", ".join(f"{n}={len(b):,}c" for n, b in drafts.items())
    console.print(
        f"  -> wrote [white]docs/[/white] ({len(drafts)} files: {sizes})  "
        f"[dim]({time.time()-t:.1f}s)[/dim]"
    )
    if errors:
        console.print(f"  [yellow]({len(errors)} non-fatal errors logged to errors.log)[/yellow]")


def _run_baseline(repo: Path, out: Path, manifest) -> None:
    console.print("\n[bold cyan]baseline[/bold cyan] one-shot README ...")
    t = time.time()
    try:
        result = run_baseline(manifest)
    except LLMError as e:
        console.print(f"[red]baseline failed: {e}[/red]")
        return

    out_md = out / "docs" / "README_baseline.md"
    out_md.write_text(result.markdown, encoding="utf-8")
    (out / "docs" / "README_baseline.meta.json").write_text(
        json.dumps(
            {
                "files_included": result.files_included,
                "prompt_chars": result.prompt_chars,
                "duration_sec": round(time.time() - t, 2),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    console.print(
        f"  -> wrote [white]docs/README_baseline.md[/white] "
        f"({len(result.markdown):,} chars from {len(result.files_included)} files)  "
        f"[dim]({time.time()-t:.1f}s)[/dim]"
    )


def _print_manifest_summary(m) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("primary", m.primary_language or "-")
    table.add_row(
        "languages",
        ", ".join(f"{k}:{v}" for k, v in sorted(m.languages.items(), key=lambda kv: -kv[1])[:6]),
    )
    table.add_row("frameworks", ", ".join(m.frameworks) or "-")
    table.add_row("entry points", ", ".join(m.entry_points[:5]) or "-")
    table.add_row("modules", ", ".join(m.top_level_modules) or "-")
    table.add_row("license", m.license or "-")
    table.add_row(
        "tests/ci/docker",
        f"{'Y' if m.has_tests else '-'}/{'Y' if m.has_ci else '-'}/{'Y' if m.has_docker else '-'}",
    )
    console.print(table)


if __name__ == "__main__":
    app()
