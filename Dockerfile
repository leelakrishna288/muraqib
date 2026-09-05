# Multi-stage. Final image runs as a non-root user with no build toolchain,
# because a compliance tool that ships a root container is not a good look.
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .

FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Muraqib" \
      org.opencontainers.image.description="Agentic AI governance & compliance readiness assessment" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/leelakrishna288/muraqib"

RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 muraqib
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=muraqib:muraqib corpus ./corpus

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MURAQIB_CORPUS_DIR=/app/corpus/frameworks \
    MURAQIB_DATA_DIR=/app/data \
    MURAQIB_PROVIDER=offline

RUN mkdir -p /app/data && chown -R muraqib:muraqib /app/data
USER muraqib
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "muraqib.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
