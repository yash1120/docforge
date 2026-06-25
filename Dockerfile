# docforge web server image. Slim base, no dev tools, app on :8000.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOCFORGE_MAX_PARALLEL=4

# Native deps: git for cloning user repos via /api/run; build-essential for any
# packages without manylinux wheels on a slim base.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache deps as a separate layer.
COPY pyproject.toml /app/
RUN pip install --upgrade pip wheel \
    && pip install \
        "typer>=0.12" "rich>=13" "pydantic>=2.7" "python-dotenv>=1.0" \
        "httpx>=0.27" "groq>=0.11" "anthropic>=0.40" \
        "langgraph>=0.2" "langchain-core>=0.3" \
        "tree-sitter>=0.23" "tree-sitter-python>=0.23" "tree-sitter-typescript>=0.23" \
        "tree-sitter-javascript>=0.23" \
        "chromadb>=0.5" "fastembed>=0.4" \
        "fastapi>=0.115" "uvicorn[standard]>=0.30" "jinja2>=3.1" "sse-starlette>=2.1"

# Now copy the project + install in editable mode.
COPY src /app/src
COPY README.md /app/README.md
# Baked-in demo data so the site shows real output without an API key, plus the
# eval testset for the scoreboard route.
COPY examples /app/examples
COPY eval /app/eval
RUN pip install -e .

# World-writable cache dir so the pre-warmed model (baked at build time as root)
# is still found at runtime on hosts that run the container as a non-root user
# (e.g. Hugging Face Spaces runs as uid 1000). Keeps build == runtime cache path.
ENV HF_HOME=/app/.cache \
    XDG_CACHE_HOME=/app/.cache \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN mkdir -p /app/.cache/fastembed && chmod -R 777 /app/.cache

# Pre-warm the embedding model so first request isn't a 90-second wait.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" \
    || echo "embed model warm-up skipped"

# $PORT is injected by most PaaS (Render, Railway, Heroku, Fly). Default 8000.
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT expands at runtime. docforge-serve reads $PORT itself.
# Single worker: the pipeline is memory-heavy and the platform scales by machine.
CMD ["sh", "-c", "docforge-serve --host 0.0.0.0 --port ${PORT:-8000}"]
