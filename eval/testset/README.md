# Testset

Each subdirectory is one repo under test. The plan locks **5 repos × 20 claims = 100 hand-labelled ground-truth claims** as the W3 bar for a defensible scoreboard. The Week-3 seed is the `daimon/` entry plus a documented expansion target list.

## Format

```
<repo-name>/
  meta.json       # repo metadata
  claims.jsonl    # one ground-truth claim per line
```

### `meta.json`

```json
{
  "name": "fastapi",
  "git_url": "https://github.com/tiangolo/fastapi",
  "primary_language": "Python",
  "runtime_topology": "library"
}
```

You may use `repo_path` (absolute local path) instead of `git_url`.

### `claims.jsonl`

```jsonl
{"id": "fastapi-01", "claim": "FastAPI routes are declared with @app.get / @app.post decorators.", "expected_files": ["fastapi/applications.py"], "module": "fastapi"}
{"id": "fastapi-02", "claim": "Dependency injection is implemented in fastapi/dependencies/utils.py.", "expected_files": ["fastapi/dependencies/utils.py"]}
```

Required: `id` (unique), `claim` (sentence). Optional: `expected_files`, `module`.

## Expansion target (per plan)

| Repo | Language | Status |
|---|---|---|
| FastAPI | Python | TODO — hand-label 20 |
| ripgrep | Rust | TODO — hand-label 20 |
| Hugo | Go | TODO — hand-label 20 |
| Next.js | polyglot | TODO — hand-label 20 |
| (a TS library) | TypeScript | TODO — hand-label 20 |
| daimon | Python | **seeded** with 5 claims; expand to 20 |

## How to run

```bash
docforge-eval --testset eval/testset --out eval/scoreboard_data.json
```

(See `src/docforge/evals/runner.py` and the `docforge-eval` console script.)
