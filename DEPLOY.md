# Deploy

docforge ships as a single FastAPI service. Two paths supported: **Fly.io** (recommended — cheap, fast cold start, single command) and a generic Docker host.

## Fly.io (recommended)

One-time setup:

```bash
# Install fly CLI: https://fly.io/docs/hands-on/install-flyctl/
fly auth login
fly apps create docforge   # or: fly launch --copy-config --no-deploy
fly secrets set GROQ_API_KEY=$GROQ_API_KEY \
                ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
```

Deploy:

```bash
fly deploy
fly open
```

Tweak `fly.toml`:

- **`primary_region`** — defaults to `syd` (Sydney). Change to your nearest Fly region.
- **`[[vm]] memory`** — `1gb` is comfortable for ~50k LOC repos; bump to `2gb` if you're hitting OOM during indexing.
- **`auto_stop_machines = "stop"`** — idle machines sleep within ~5 min. First request after sleep adds ~3-5s cold start.

### Secrets

| Name | Required | Purpose |
|---|---|---|
| `GROQ_API_KEY` | yes (primary path) | Llama/Qwen calls for Reader/Writer/Critic/Editor |
| `ANTHROPIC_API_KEY` | fallback if Groq absent + required to run `docforge-eval` (Sonnet judge) | LLM client + Sonnet 4.6 judge |
| `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` | optional | Trace every agent call to a Langfuse dashboard |

Never bake these into the image. Fly secrets are encrypted and injected as env vars at runtime.

## Plain Docker

```bash
docker build -t docforge .
docker run --rm -p 8000:8000 \
    -e GROQ_API_KEY=$GROQ_API_KEY \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    docforge
# open http://localhost:8000
```

## Local development

```bash
python -m venv .venv
.venv/Scripts/activate         # Windows
source .venv/bin/activate      # macOS/Linux
pip install -e .[dev]
docforge-serve --reload
```

## Operating notes

- **Embedding model is pre-warmed** in the Dockerfile (`bge-small-en-v1.5`, ~30 MB). If the build-time download is blocked, the first request to `/api/run` will eat the warm-up cost (~60-90s) once per machine.
- **Per-request memory** scales with repo size. A 50k LOC repo creates ~3-4k chunks in Chroma; 8k LOC repos like daimon stay well under 200 MB.
- **`auto_stop_machines`** means cold starts. Don't enable it for a demo people will hit live — set `min_machines_running = 1` for the showcase.
- **Job state is in-memory.** A machine restart loses queued/completed jobs (the docs are still on the cloned-repo disk under `/tmp/docforge-runs`, which Fly clears between machines). For persistence, swap `JobRegistry` for a sqlite or Redis-backed implementation.
