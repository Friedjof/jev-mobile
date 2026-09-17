# Jev Mobile MCP server. Device access is supplied at runtime (for example,
# with an ADB server mount or forwarding); the image contains no credentials.
FROM ghcr.io/astral-sh/uv:0.10.7 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENTRYPOINT ["jev-mobile-mcp"]
