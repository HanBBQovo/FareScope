# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.7.8

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv


FROM python:${PYTHON_VERSION}-slim-bookworm AS dependency-builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/farescope

WORKDIR /build

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./
COPY app ./app

RUN uv sync --frozen --no-dev --no-editable


FROM dependency-builder AS collector-dependency-builder

RUN uv sync --frozen --no-dev --no-editable --extra collector


FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime-base

ENV PATH="/opt/farescope/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/farescope

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 farescope \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/farescope \
        --shell /usr/sbin/nologin farescope \
    && mkdir -p /var/lib/farescope/celerybeat /var/lib/farescope/collector-artifacts \
    && chown -R 10001:10001 /var/lib/farescope

WORKDIR /app

COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 alembic ./alembic

ENTRYPOINT ["/usr/bin/tini", "--"]


FROM runtime-base AS runtime

COPY --from=dependency-builder --chown=10001:10001 /opt/farescope /opt/farescope

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host=0.0.0.0", "--port=8000", "--workers=2", "--proxy-headers", "--forwarded-allow-ips=*"]


FROM runtime-base AS collector-runtime-chromium

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/tmp/farescope-home \
    FARESCOPE_COLLECTOR_BROWSER_CHANNEL=chromium

COPY --from=collector-dependency-builder --chown=10001:10001 /opt/farescope /opt/farescope
COPY --chown=10001:10001 scripts/collector/runtime_smoke.py /app/scripts/collector/runtime_smoke.py
COPY --chown=10001:10001 scripts/collector/entrypoint.sh /app/scripts/collector/entrypoint.sh

RUN apt-get update \
    && apt-get install --yes --no-install-recommends xauth xvfb \
    && /opt/farescope/bin/python -m playwright install --with-deps chromium \
    && chmod 0755 /app/scripts/collector/entrypoint.sh \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

USER 10001:10001

ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/collector/entrypoint.sh"]

CMD ["celery", "-A", "app.tasks.celery_app:celery_app", "worker", "--loglevel=INFO", "--queues=collector", "--concurrency=1"]


FROM collector-runtime-chromium AS collector-runtime

USER root

RUN /opt/farescope/bin/python -m playwright install --with-deps chrome \
    && google-chrome --version \
    && rm -rf /var/lib/apt/lists/*

ENV FARESCOPE_COLLECTOR_BROWSER_CHANNEL=chrome

USER 10001:10001
