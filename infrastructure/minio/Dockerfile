# syntax=docker/dockerfile:1
# Community MinIO is source-only; do not depend on the retired minio/minio image.
FROM golang:1.26.9-bookworm AS build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
WORKDIR /src/minio
# Source of RELEASE.2025-10-15T17-29-55Z, including its session-policy security fix.
RUN git init . \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth=1 origin 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    MINIO_RELEASE=RELEASE go build -tags kqueue -trimpath \
      -ldflags "$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go 2025-10-15T17:29:55Z)" \
      -o /out/minio .

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
      org.opencontainers.image.version="RELEASE.2025-10-15T17-29-55Z" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/minio/LICENSE /usr/share/licenses/minio/LICENSE
EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data"]
