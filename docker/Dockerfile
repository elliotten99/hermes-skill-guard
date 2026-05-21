# ---------- build stage ----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy source code and install the project
COPY src/ ./src/
COPY plugin.yaml ./
COPY skills/ ./skills/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------- runtime stage ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy only the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application data files needed at runtime
COPY --from=builder /app/plugin.yaml ./
COPY --from=builder /app/skills/ ./skills/

# Create non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup appuser
USER appuser

ENTRYPOINT ["hermes-skill-guard"]
CMD ["--help"]
