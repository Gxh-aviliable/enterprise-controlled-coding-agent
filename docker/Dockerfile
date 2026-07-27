FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install locked third-party dependencies before copying application source so
# ordinary code edits do not invalidate the expensive PyTorch/Chroma layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --link-mode=copy

# Create the unprivileged runtime user and only chown directories it must write.
# Chowning the full /app/.venv after every source edit traversed the large
# PyTorch dependency layer and made small rebuilds take several minutes.
RUN useradd --create-home --uid 10001 agent && \
    mkdir -p /workspaces /data/chroma /data/huggingface && \
    chown -R agent:agent /workspaces /data

# Copy and install only the local project after the reusable dependency layer.
COPY enterprise_agent ./enterprise_agent
COPY shared_skills ./shared_skills
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --offline --no-build-isolation --link-mode=copy

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WORKSPACE_BASE=/workspaces \
    CHROMA_PERSIST_DIR=/data/chroma \
    HF_HOME=/data/huggingface \
    SHARED_SKILLS_DIR=/app/shared_skills \
    MANAGED_SHARED_SKILLS_DIR=/data/shared-skills

USER agent

# Expose port
EXPOSE 8000

# Run
CMD ["sh", "-c", "/app/.venv/bin/alembic upgrade head && exec /app/.venv/bin/python -m uvicorn enterprise_agent.api.main:app --host 0.0.0.0 --port 8000"]
