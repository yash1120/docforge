# Deploy

docforge ships as a single FastAPI service (`docforge.server.app:app`). It runs
the same agent pipeline behind a web UI, plus a baked-in real example run and the
eval scoreboard. The site works **with no API key** — the explainer, the
`/example` run, and `/scoreboard` all render statically; only the live "paste a
GitHub URL" path needs a key at request time.

Four supported targets: **Fly.io**, **Render**, **Railway**, and **generic
Docker / docker-compose**. All four use the same `Dockerfile`. The container
binds `$PORT` (falling back to 8000), so the PaaS platforms that inject a random
port just work.

---

## Fly.io

```bash
# Install flyctl: https://fly.io/docs/hands-on/install-flyctl/
fly auth login
fly apps create docforge                 # or: fly launch --copy-config --no-deploy
fly secrets set GROQ_API_KEY=$GROQ_API_KEY ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
fly deploy
fly open
```

Tweaks in `fly.toml`:
- `primary_region` — defaults to `syd` (Sydney). Change to your nearest region.
- `[[vm]] memory` — `1gb` is fine for ~50k LOC; bump to `2gb` if indexing OOMs.
- For a live demo, set `min_machines_running = 1` so visitors don't hit a
  cold start. (`auto_stop_machines = "stop"` saves money but adds ~3–5s.)

## Render (one-click Blueprint)

A `render.yaml` Blueprint is included.

1. Push the repo to GitHub.
2. Render dashboard → **New +** → **Blueprint** → pick the repo. It reads `render.yaml`.
3. In the service's **Environment** tab, set `GROQ_API_KEY` and `ANTHROPIC_API_KEY` (both `sync: false`, so you enter them by hand).
4. Deploy. Health check is `/api/health`; Render injects `$PORT` and the container honors it.

> If your `Dockerfile` is not at the repo root, set the service **Root Directory** to the folder that contains it.

## Railway

1. `railway init`, or connect the GitHub repo in the Railway dashboard.
2. Railway auto-detects the `Dockerfile` (no nixpacks needed). If the Dockerfile isn't at repo root, set the service root/build context accordingly.
3. **Variables** tab: add `GROQ_API_KEY` and `ANTHROPIC_API_KEY`.
4. **Settings → Networking**: generate a public domain. Railway maps it to `$PORT`, which the container binds.

## Generic Docker / docker-compose

```bash
# Plain docker
docker build -t docforge .
docker run --rm -p 8000:8000 \
    -e GROQ_API_KEY=$GROQ_API_KEY \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    docforge
# → http://localhost:8000

# Or compose (reads keys from your shell / a gitignored .env)
docker compose up --build
```

`docker-compose.yml` adds a restart policy and a `/api/health` healthcheck.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate          # macOS/Linux;  .venv/Scripts/activate on Windows
pip install -e ".[dev]"
docforge-serve --reload            # http://localhost:8000
```

---

## Secrets

| Name | Required? | Purpose |
|---|---|---|
| `GROQ_API_KEY` | for live runs (primary path) | Llama/Qwen calls for Reader/Writer/Critic/Editor |
| `ANTHROPIC_API_KEY` | fallback for live runs; required for `docforge-eval` | LLM client fallback + Sonnet 4.6 eval judge |
| `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` | optional | trace every agent call to Langfuse |
| `DOCFORGE_MAX_PARALLEL` | optional (default 4) | bound per-agent thread-pool concurrency; set `2` on free tiers |
| `PORT` | injected by the platform | bind port; falls back to 8000 |
| `DOCFORGE_SCOREBOARD` | optional | override the scoreboard JSON path |
| `DOCFORGE_EXAMPLES` | optional | override the baked-examples dir |

Never bake keys into the image — inject them as platform secrets at runtime.

## Demo mode (no key at request time)

The deployed site is useful even with **zero keys**:

- **`/`** — the full explainer renders statically.
- **`/example`** — a real docforge run against the [daimon](examples/daimon/run.json)
  repo, baked into the image at `examples/daimon/`: the generated `API.md` and
  `ARCHITECTURE.md`, the Mermaid diagram, the scout counts, the critic's real
  verdict (factuality 40%, coverage 47%), **and** the honest rate-limit failure
  case. No LLM call, no latency.
- **`/scoreboard`** — renders real numbers if you commit an `eval/scoreboard_data.json`
  (generate it once locally with `docforge-eval`); otherwise a clear placeholder.

When no provider key is configured, `POST /api/run` returns a friendly **503**
("see /example for a real run") instead of a stack trace, and the landing page
relabels the live-run form. So an anonymous visitor can't burn your Groq quota.

## Operating notes

- **Embedding model is pre-warmed** in the Dockerfile (`bge-small-en-v1.5`, ~30 MB). If the build-time download is blocked, the first live run eats the warm-up (~60–90s) once per machine.
- **Per-request memory** scales with repo size. ~8k LOC (daimon) stays under 200 MB; a 50k LOC repo wants ≥1 GB.
- **Job state is in-memory.** A restart drops queued/finished live jobs (cloned repos live under the OS temp dir). The baked `/example` survives restarts because it's read from disk. For durable live jobs, swap `JobRegistry` for sqlite/Redis.
- **Free-tier rate limits are real.** The daimon run hit a Groq 429 (TPM cap 12000) — set `DOCFORGE_MAX_PARALLEL=2` and prefer small repos on free tiers.
