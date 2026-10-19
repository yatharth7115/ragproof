# Local MinIO source image

Both Compose stacks build `ragproof-minio:2025-10-15-source` from this directory.
The retired `minio/minio` image is no longer needed. The first build downloads
the upstream Go modules and compiles MinIO; later builds reuse Docker caches.

Upstream source is pinned to commit
`9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`, the source of
[`RELEASE.2025-10-15T17-29-55Z`](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z).
The Dockerfile verifies the checked-out commit, uses the upstream build flags,
and copies the upstream AGPL license into the image. Source is available at
[`minio/minio` at that commit](https://github.com/minio/minio/tree/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a).
No third-party mirror or precompiled MinIO download is used.

The builder uses the official `golang:1.26.9-bookworm` image, with automatic Go
toolchain downloads disabled. Runtime uses `debian:bookworm-slim`, CA certificates,
and curl for the existing health probe. Base-image patch updates and Debian
packages are not content-addressed; the pinned application source does not by
itself make the entire image bit-for-bit reproducible.

Start the disposable laboratory from the repository root:

```console
docker compose -f infrastructure/docker-compose.lab.yml config --quiet
docker compose -f infrastructure/docker-compose.lab.yml up -d --build --wait --wait-timeout 180
```

The application stack uses the same MinIO build; configure its credentials as
described in `docs/local-auth-setup.md`. Service names, ports, and named volumes
are preserved. Do not remove volumes from an existing working installation.
Use a separately named Compose project and empty volumes for upgrade validation;
take a backup before deliberately upgrading a populated object store.

Community MinIO is now an archived, unsupported upstream. This image restores
the local test dependency and includes the final published security release;
it is not a maintained production storage recommendation. Selecting a maintained
production object store and validating migration remains an operational decision.
