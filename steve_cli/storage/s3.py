from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointConnectionError

from .branch import get_branch_prefix
from steve_cli.auth import get_s3_endpoint

logger = logging.getLogger(__name__)


class S3Storage:
    def __init__(self, tier: str = "bronze", workspace: str | None = None):
        self.tier = tier.lower()
        self._branch = get_branch_prefix()
        self.endpoint, self.bucket, access_key, secret_key, required_vars = (
            self._load_config(self.tier, workspace)
        )

        cred_endpoint = get_s3_endpoint()
        if cred_endpoint:
            self.endpoint = cred_endpoint
            self._endpoint_source = "credentials"
        else:
            self._endpoint_source = "env" if os.getenv("S3_ENDPOINT") else "default"

        missing = [v for v in required_vars if not os.getenv(v)]
        if missing:
            raise OSError(
                f"Missing required environment variables for {self.tier} tier: "
                f"{', '.join(missing)}"
            )

        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            region_name="us-east-1",
        )

    @staticmethod
    def _load_config(
        tier: str, workspace: str | None
    ) -> tuple[str, str, str, str, list[str]]:
        if workspace:
            prefix = workspace.upper().replace("-", "_")
            tier_name = tier.upper()
            endpoint = os.getenv(
                f"{prefix}_S3_ENDPOINT",
                os.getenv("S3_ENDPOINT", "http://localhost:9000"),
            )
            access_key = os.getenv(f"{prefix}_ACCESS_KEY")
            secret_key = os.getenv(f"{prefix}_SECRET_KEY")
            bucket = os.getenv(f"{prefix}_BUCKET_{tier_name}")
            required = [
                f"{prefix}_ACCESS_KEY",
                f"{prefix}_SECRET_KEY",
                f"{prefix}_BUCKET_{tier_name}",
            ]
        else:
            tier_name = tier.upper()
            endpoint = os.getenv("S3_ENDPOINT", "http://localhost:9000")
            access_key = os.getenv(f"{tier_name}_ACCESS_KEY")
            secret_key = os.getenv(f"{tier_name}_SECRET_KEY")
            bucket = os.getenv(f"{tier_name}_BUCKET")
            required = [
                f"{tier_name}_ACCESS_KEY",
                f"{tier_name}_SECRET_KEY",
                f"{tier_name}_BUCKET",
            ]
        return endpoint, bucket, access_key, secret_key, required

    def _key(self, path: str) -> str:
        if not path:
            return f"_store/{self._branch}/"
        clean = str(PurePosixPath(path).as_posix().lstrip("/"))
        return f"_store/{self._branch}/{clean}"

    def _object_location(self, path: str) -> str:
        return f"{self.endpoint}/{self.bucket}/{self._key(path)}"

    def _handle_client_error(self, error: ClientError, path: str, operation: str) -> None:
        code = error.response["Error"]["Code"]
        key = self._key(path)
        tier_upper = self.tier.upper()
        location = f"s3://{self.bucket}/{key} at {self.endpoint}"
        if code == "AccessDenied":
            logger.error("S3Storage: Access Denied when %s [%s]", operation, location)
            raise OSError(
                f"S3Storage: {operation} failed — Access Denied [{location}] "
                f"(check {tier_upper}_ACCESS_KEY / {tier_upper}_SECRET_KEY have permission)"
            )
        elif code == "NoSuchBucket":
            logger.error("S3Storage: bucket not found [bucket=%s, endpoint=%s]", self.bucket, self.endpoint)
            raise OSError(
                f"S3Storage: {operation} failed — bucket '{self.bucket}' not found at {self.endpoint}"
            )
        elif code == "NoSuchKey":
            logger.warning("S3Storage: key not found when %s [%s]", operation, location)
            raise OSError(
                f"S3Storage: {operation} failed — key not found [s3://{self.bucket}/{key}]"
            )
        else:
            logger.error("S3Storage: %s error when %s [%s]: %s", code, operation, location, error)
            raise OSError(
                f"S3Storage: {operation} failed — {code} [s3://{self.bucket}/{key} at {self.endpoint}]"
            )

    def put_file(self, local_path: str, path: str) -> None:
        try:
            self.client.upload_file(local_path, self.bucket, self._key(path))
            logger.info("Uploaded %s", self._object_location(path))
        except EndpointConnectionError:
            raise OSError(f"S3Storage: cannot connect to S3 endpoint at {self.endpoint} (uploading {path})")
        except ClientError as e:
            self._handle_client_error(e, path, "uploading")

    def get_file(self, path: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download_file(self.bucket, self._key(path), local_path)
            logger.info("Downloaded %s", self._object_location(path))
        except EndpointConnectionError:
            raise OSError(f"S3Storage: cannot connect to S3 endpoint at {self.endpoint} (downloading {path})")
        except ClientError as e:
            self._handle_client_error(e, path, "downloading")

    def put_bytes(self, data: bytes, path: str) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=self._key(path), Body=data)
            logger.info("Stored %s", self._object_location(path))
        except EndpointConnectionError:
            raise OSError(f"S3Storage: cannot connect to S3 endpoint at {self.endpoint} (writing {path})")
        except ClientError as e:
            self._handle_client_error(e, path, "writing")

    def get_bytes(self, path: str) -> bytes:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._key(path))
            logger.info("Retrieved %s", self._object_location(path))
            return obj["Body"].read()
        except EndpointConnectionError:
            raise OSError(f"S3Storage: cannot connect to S3 endpoint at {self.endpoint} (reading {path})")
        except ClientError as e:
            self._handle_client_error(e, path, "reading")

    def list(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(prefix)):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys

    def list_all(self) -> list[str]:
        return self.list()
