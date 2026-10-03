FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
COPY pyproject.toml setup.py MANIFEST.in README.md THIRD_PARTY_NOTICES.md constraints.txt ./
COPY src ./src
COPY schemas ./schemas
COPY fixtures ./fixtures
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels -c constraints.txt '.[api,storage,otlp,integrations]'

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 ragproof && useradd --uid 10001 --gid 10001 --no-create-home ragproof
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels 'ragproof-mvp[api,storage,otlp,integrations]' && rm -r /wheels
USER 10001:10001
WORKDIR /app
EXPOSE 8080
CMD ["uvicorn", "ragproof_store.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
