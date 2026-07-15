FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY enterprise_agent ./enterprise_agent

# Install dependencies with uv
RUN uv sync --frozen --no-dev

# Environment
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8000

# Run
CMD ["uv", "run", "serve"]
