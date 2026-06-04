# docforge

> Point at any repo. Get an honest `docs/` folder back. Multi-agent. Grounded.

`docforge` runs a supervised team of small LLM agents over your codebase and produces a README, an `ARCHITECTURE.md`, an `API.md`, an onboarding tutorial, and a Mermaid diagram — with every non-trivial claim grounded in `[file:line]` citations and verified by a critic loop.

```bash
pip install -e .
docforge ./path/to/repo
# → ./path/to/repo/.docforge/docs/{README,ARCHITECTURE,API,TUTORIAL}.md
```

**Status — Week 4 of 4.** `[x] scaffold  [x] scout  [x] indexer  [x] baseline  [x] agent team  [x] critic + editor + eval harness  [x] parallel scouts (TestScout / APIScanner / ConfigReader) + threaded LLM agents  [ ] ship (web UI + showcase + blog)`

98/98 tests green. The graph now fans out three static scouts in parallel from `START` (TestScout, APIScanner, ConfigReader), feeds their outputs into the LLM agents, and each LLM agent (Reader / Writer / Critic) parallelizes its inner loop via a bounded thread pool. `docforge-eval` produces a 5-axis scoreboard. Seed testset: daimon (5 of 20 claims). Plan target: 5 repos × 20 claims.

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

See `plan.md` for the full build plan.

## License

MIT.
