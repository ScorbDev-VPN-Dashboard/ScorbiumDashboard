FROM python:3.13-slim

WORKDIR /app

# Install system deps for pg_dump (backup feature)
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (layer cache)
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy source
COPY . .

EXPOSE 8000

# Run combined API + Bot
CMD ["uv", "run", "python", "main.py"]
