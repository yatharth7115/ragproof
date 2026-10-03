from __future__ import annotations

import hashlib
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import StorageSettings


class ContentObjectStore:
    def __init__(self, settings: StorageSettings) -> None:
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", connect_timeout=2, read_timeout=10, retries={"max_attempts": 2}),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    @staticmethod
    def key_for(tenant_id: str, project_id: str, content_hash: str) -> str:
        namespace = hashlib.sha256(f"{tenant_id}\0{project_id}".encode()).hexdigest()[:24]
        return f"content/{namespace}/{content_hash}.txt"

    def put_text(self, tenant_id: str, project_id: str, content_hash: str, content: str) -> str:
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_hash != content_hash:
            raise ValueError("Content does not match its declared SHA-256 hash")
        key = self.key_for(tenant_id, project_id, content_hash)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
            Metadata={"sha256": content_hash},
        )
        return f"s3://{self.bucket}/{key}"

    def get_text(self, reference: str) -> str:
        parsed = urlparse(reference)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            raise ValueError("Object reference is outside the configured bucket")
        response = self.client.get_object(Bucket=self.bucket, Key=parsed.path.lstrip("/"))
        return response["Body"].read().decode("utf-8")
