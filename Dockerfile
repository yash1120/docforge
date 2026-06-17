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
RUN pip install -e .

# Pre-warm the embedding model so first request isn't a 90-second wait.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" \
    || echo "embed model warm-up skipped"

EXPOSE 8000

# Default to the production-ish single-worker setup. Fly autoscales by machine,
# not by uvicorn worker, so workers=1 is the right default here.
CMD ["uvicorn", "docforge.server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
