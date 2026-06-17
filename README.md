# docforge

> Point at any repo. Get an honest `docs/` folder back. Multi-agent. Grounded.

`docforge` runs a supervised team of small LLM agents over your codebase and produces a README, an `ARCHITECTURE.md`, an `API.md`, an onboarding tutorial, and a Mermaid diagram — with every non-trivial claim grounded in `[file:line]` citations and verified by a critic loop.

```bash
pip install -e .
docforge ./path/to/repo
# → ./path/to/repo/.docforge/docs/{README,ARCHITECTURE,API,TUTORIAL}.md
```

**Status — Week 4 shipped.** `[x] scaffold  [x] scout  [x] indexer  [x] baseline  [x] agent team  [x] critic + editor + eval harness  [x] parallel scouts + threaded LLM agents  [x] web UI + Docker + Fly + self-docs GH Action + blog draft`

117/117 tests green. Three CLI entrypoints: `docforge` (full pipeline), `docforge-eval` (5-axis scoreboard), `docforge-serve` (FastAPI web UI at `/`, `/run/{id}`, `/scoreboard`, `/showcase`).

### Agent graph

```
START
  ├── test_scout ─────┐
  ├── api_scanner ────┼──► reader ──► architect ──► diagrammer ──► writer ──► critic
  └── config_reader ──┘                                                          │
                                                                                 ▼
                                  (issues>0 and cycles<MAX ? editor ──► critic : END)
```

Tune parallelism via `DOCFORGE_MAX_PARALLEL` (default 4; set to 1 for serial debugging).

### Quickstart

```bash
pip install -e .
docforge ./path/to/repo                    # CLI: scout + index + agent team + critic loop
docforge-serve --reload                    # web UI on :8000
docforge-eval --testset eval/testset       # run the 5-axis eval against your testset
```

### Deploy

See `DEPLOY.md`. TL;DR: `fly secrets set GROQ_API_KEY=...` then `fly deploy`.

### Self-documenting + eval-on-PR

- `.github/workflows/docs.yml` regenerates `docs/` on every push to main.
- `.github/workflows/eval.yml` runs `docforge-eval` on every PR and fails the check if any axis regresses >2pp vs main.
- `.github/workflows/ci.yml` runs ruff + the full test suite.

## Repo map

| Path | What |
|---|---|
| `src/docforge/scout/` | Static repo walker → `Manifest` |
| `src/docforge/indexer/` | tree-sitter chunk + `bge-small` embed → Chroma |
| `src/docforge/agents/` | 9 agents + LangGraph supervisor + critic loop |
| `src/docforge/evals/` | Sonnet 4.6 judge, 5-axis scoreboard |
| `src/docforge/server/` | FastAPI + Jinja2 web UI |
| `eval/testset/` | Ground-truth claims for the scoreboard |
| `plan.md` | Full 4-weekend build plan |
| `ARCHITECTURE.md` | How the pieces fit together (human-written) |
| `CONTRIBUTING.md` | Dev loop + PR checklist |
| `CHANGELOG.md` | What shipped |
| `DEPLOY.md` | Fly.io + plain Docker |
| `BLOG.md` | Engineering write-up (draft) |

## License

MIT.
