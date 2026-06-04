# docforge

> Point at any repo. Get an honest `docs/` folder back. Multi-agent. Grounded.

`docforge` runs a supervised team of small LLM agents over your codebase and produces a README, an `ARCHITECTURE.md`, an `API.md`, an onboarding tutorial, and a Mermaid diagram — with every non-trivial claim grounded in `[file:line]` citations and verified by a critic loop.

```bash
pip install -e .
docforge ./path/to/repo
# → ./path/to/repo/.docforge/docs/{README,ARCHITECTURE,API,TUTORIAL}.md
```

**Status — Week 3 of 4.** `[x] scaffold  [x] scout  [x] indexer  [x] baseline  [x] agent team  [x] critic + editor + eval harness  [ ] ship (web UI + showcase + blog)`

79/79 tests green. Critic-loop bounded to 2 cycles. `docforge-eval` CLI runs the 5-axis scoreboard (factuality / coverage / completeness / citation density / readability) over `eval/testset/`. Seed testset: daimon (5 of 20 claims). Plan target: 5 repos × 20 claims.

See `plan.md` for the full build plan.

## License

MIT.
