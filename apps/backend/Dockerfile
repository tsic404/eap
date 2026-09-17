FROM python:3.12-slim AS builder
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# Standalone uv binary from its official distroless image; pin for reproducibility.
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /bin/

# Locked production deps only, layered ahead of the source so this caches.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Install the project itself (non-editable, so the venv is self-contained).
COPY . .
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
# Alembic needs its config and script tree in the image to run migrations at
# container start (see docker-entrypoint.sh).
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 3001
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3001/api/health/live')"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
