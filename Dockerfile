# Multi-stage build: slim runtime, no build tools in final image.
FROM python:3.13-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Copy only what's needed to resolve dependencies first (cache-friendly).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --prefix=/install --no-deps . \
 && pip install --prefix=/install "mcp>=1.2.0" "psycopg[binary]>=3.2.0"


FROM python:3.13-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/sarteta/mcp-postgres-doctor"
LABEL org.opencontainers.image.description="Read-only Postgres operational diagnostics over MCP"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root user — MCP server must never run as root.
RUN groupadd --system --gid 10001 mcp \
 && useradd  --system --uid 10001 --gid mcp --create-home mcp

COPY --from=builder /install /usr/local

USER mcp
WORKDIR /home/mcp

# DATABASE_URL must be provided at runtime, e.g.:
#   docker run -e DATABASE_URL=postgresql://... ghcr.io/sarteta/mcp-postgres-doctor
ENTRYPOINT ["mcp-postgres-doctor"]
