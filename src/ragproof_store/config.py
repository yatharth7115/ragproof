from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class StorageSettings:
    postgres_dsn: str = "postgresql://ragproof:local-development-only@127.0.0.1:55432/ragproof_lab"
    redis_url: str = "redis://127.0.0.1:56379/0"
    s3_endpoint_url: str = "http://127.0.0.1:59000"
    s3_access_key: str = "ragproof-local"
    s3_secret_key: str = "ragproof-local-development-only"
    s3_bucket: str = "ragproof-content"
    s3_region: str = "us-east-1"

    @classmethod
    def from_env(cls) -> "StorageSettings":
        defaults = cls()
        return cls(
            postgres_dsn=os.getenv("RAGPROOF_POSTGRES_DSN", defaults.postgres_dsn),
            redis_url=os.getenv("RAGPROOF_REDIS_URL", defaults.redis_url),
            s3_endpoint_url=os.getenv("RAGPROOF_S3_ENDPOINT_URL", defaults.s3_endpoint_url),
            s3_access_key=os.getenv("RAGPROOF_S3_ACCESS_KEY", defaults.s3_access_key),
            s3_secret_key=os.getenv("RAGPROOF_S3_SECRET_KEY", defaults.s3_secret_key),
            s3_bucket=os.getenv("RAGPROOF_S3_BUCKET", defaults.s3_bucket),
            s3_region=os.getenv("RAGPROOF_S3_REGION", defaults.s3_region),
        )
