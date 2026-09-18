# OpenClaw owns the stdio child process.  This overlay deliberately contains
# only Jev Mobile's MCP runtime; Android tooling and USB access stay in the
# separate jev-mobile worker image.
# OpenClaw 2026.9.4 is Debian bookworm; pin the Python runtime to the same
# libc generation rather than following the moving slim tag.
FROM python:3.12-slim-bookworm AS python_runtime

FROM ghcr.io/astral-sh/uv:0.10.7 AS uv

FROM ghcr.io/openclaw/openclaw:2026.9.4

USER root

# The OpenClaw image is Debian bookworm too, so the CPython runtime can be
# reused. Build the locked MCP runtime from this checkout rather than relying
# on a daemon-local Docker image being visible to BuildKit.
COPY --from=python_runtime /usr/local /usr/local
COPY --from=uv /uv /uvx /bin/
WORKDIR /opt/jev-mobile
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY deploy/openclaw/jev-mobile-mcp /usr/local/bin/jev-mobile-mcp

RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.12 \
    && chmod 0755 /usr/local/bin/jev-mobile-mcp \
    && install -d -o node -g node /data

ENV PATH="/opt/jev-mobile/.venv/bin:${PATH}" \
    PYTHONPATH="/opt/jev-mobile/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Preserve the upstream OpenClaw relative gateway command (dist/index.js).
WORKDIR /app
USER node
