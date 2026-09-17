# Runtime image only. Android/Gradle fixture builds stay on the development
# host; production images contain Python, Jev Mobile and platform-tools.
FROM ghcr.io/astral-sh/uv:0.10.7 AS uv

FROM python:3.12-slim

ARG JEV_UID=1000
ARG JEV_GID=1000

RUN apt-get update \
    && apt-get install -y --no-install-recommends adb ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${JEV_GID}" jev \
    && useradd --uid "${JEV_UID}" --gid "${JEV_GID}" --create-home --home-dir /home/jev jev \
    && install -d -o jev -g jev /data /app

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/jev \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev && chown -R jev:jev /app

USER jev
ENTRYPOINT ["jev-mobile"]
CMD ["--help"]
