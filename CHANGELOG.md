# Changelog

All notable changes to docforge. Dates in YYYY-MM-DD. Versions follow SemVer.

## 0.1.0 — 2026-06-05

First feature-complete cut. Engineering side of the [4-weekend plan](plan.md) is done; remaining items (live deploy, real eval numbers, Loom, blog publication) require external accounts or manual work.

### Added

#### Pipeline
- `Scout` — file walker + language/framework/entry-point/license detection. Skips `.venv`, `node_modules`, build artifacts. Public-API extraction excludes tests.
- `Indexer` — tree-sitter function-level chunking (Python/TypeScript/JavaScript) with sliding-window fallback for other languages. `fastembed` + `BAAI/bge-small-en-v1.5`. Chroma local persistence. Chunk IDs are stable across re-indexes.
- `Baseline` — single-agent README generator. The shame line every multi-agent improvement is measured against.

#### Agent team (LangGraph supervisor)
- `Reader` — per-module RAG summary, runs concurrently across modules.
- `Architect` — structured Architecture JSON (components, edges, external deps, runtime topology). Drops edges that reference missing components.
- `Diagrammer` — deterministic Mermaid first (always-valid), optional LLM beautify pass that's accepted only if it also validates.
- `Writer` — drafts README, ARCHITECTURE, API, TUTORIAL. Citation rule enforced in every prompt. Runs the four docs concurrently.
- `Critic` — citation validity (deterministic) + LLM grounding (parallel, cap-bounded) + coverage (deterministic word-boundary match) + uncited-claim heuristic.
- `Editor` — revises drafts by issue. Loop bounded to `MAX_CRITIC_CYCLES=2`.

#### Parallel specialist scouts (no LLM)
- `TestScout` — pytest / unittest / jest+vitest / `go test` / `cargo test` detection; per-symbol tested vs untested classification.
- `APIScanner` — HTTP routes (FastAPI/Flask/Express) and CLI commands (Click/Typer/argparse).
- `ConfigReader` — env-var reads + `.env`/`.env.example` parsing. **Redacts** values for names matching `API_KEY|SECRET|TOKEN|PASSWORD|...` and every value from a live `.env`.

#### Eval harness
- `docforge-eval` CLI runs a 5-axis scoreboard (factuality / coverage / completeness / citation density / readability) using Claude Sonnet 4.6 as the judge (locked in code).
- JSONL testset format under `eval/testset/<repo>/{meta,claims}.json{,l}`.
- Seed entry: daimon with 5 of 20 target claims.

#### Web app
- FastAPI + Jinja2 + vanilla CSS/JS — no build step.
- Routes: `/`, `/run/{id}`, `/scoreboard`, `/showcase`, `/api/run`, `/api/run/{id}/stream` (SSE), `/api/health`.
- Background `JobRegistry` runs the supervisor in a thread; per-node events stream to the client.
- `docforge-serve` CLI launches uvicorn.

#### Deploy
- `Dockerfile` (python:3.11-slim, embed-model pre-warmed at build time).
- `fly.toml` (Sydney region, auto-stop, health checks).
- `DEPLOY.md` step-by-step.

#### CI / self-docs
- `.github/workflows/ci.yml` — ruff + pytest on push/PR.
- `.github/workflows/docs.yml` — regenerates `docs/` on every push to main and commits. The meta-flex.
- `.github/workflows/eval.yml` — eval-on-PR; fails the check if any axis regresses > 2 percentage points (or > 0.1 on readability).

#### Tests
- 117 tests across W1 unit, W2 agents, W3 critic/editor/loop + eval harness, W4 parallel scouts + supervisor, web server endpoints.

### Plan deltas (worth knowing)

- `fastembed` (ONNX) instead of `sentence-transformers` (PyTorch). Lighter Docker image, faster install, same `bge-small` model.
- Individual `tree-sitter-{python,typescript,javascript}` packages instead of the omnibus `tree-sitter-languages` (better Windows wheel coverage).
- Diagrammer is deterministic-first with optional LLM beautify, validated-or-fallback, instead of LLM-then-mmdc-validate.
- Critic grounding reads files from disk directly instead of re-querying the index.
- Anthropic fallback added to the LLM client so the team can still run when only `ANTHROPIC_API_KEY` is set.

### Known limits

- Real eval numbers TBD until the testset is expanded (5×20 hand-labelled claims) and an API key is configured.
- `failures.md` ships with the three categories the project anticipates; concrete instances will be filled in after the first real eval run.
- Job state is in-memory — a server restart drops queued jobs.
