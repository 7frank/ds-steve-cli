import os
from typing import Protocol, List
from pathlib import PurePosixPath
import sys
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointConnectionError


class Storage(Protocol):
    """
    .. deprecated::
        Use ``steve_cli.storage.protocol.Storage`` instead.
    """
    def put_file(self, local_path: str, path: str) -> None: ...
    def get_file(self, path: str, local_path: str) -> None: ...
    def put_bytes(self, data: bytes, path: str) -> None: ...
    def get_bytes(self, path: str) -> bytes: ...
    def list(self, prefix: str = "") -> List[str]: ...


class S3Storage:
    """
    .. deprecated::
        Use ``steve_cli.storage.s3.S3Storage`` instead.
    """
    def __init__(self, tier: str = "bronze", workspace: str | None = None):
        self.tier = tier.lower()

        if workspace is not None:
            prefix = workspace.upper().replace("-", "_")
            tier_suffix = f"BUCKET_{tier.upper()}"
            access_key = os.getenv(f"{prefix}_ACCESS_KEY")
            secret_key = os.getenv(f"{prefix}_SECRET_KEY")
            self.bucket = os.getenv(f"{prefix}_{tier_suffix}")
            required_vars = [f"{prefix}_ACCESS_KEY", f"{prefix}_SECRET_KEY", f"{prefix}_{tier_suffix}"]
            endpoint = os.getenv(f"{prefix}_S3_ENDPOINT", os.getenv("S3_ENDPOINT", "http://localhost:9000"))
        else:
            if self.tier == "bronze":
                access_key = os.getenv("BRONZE_ACCESS_KEY")
                secret_key = os.getenv("BRONZE_SECRET_KEY")
                self.bucket = os.getenv("BRONZE_BUCKET")
                required_vars = ["BRONZE_ACCESS_KEY", "BRONZE_SECRET_KEY", "BRONZE_BUCKET"]
            elif self.tier == "silver":
                access_key = os.getenv("SILVER_ACCESS_KEY")
                secret_key = os.getenv("SILVER_SECRET_KEY")
                self.bucket = os.getenv("SILVER_BUCKET")
                required_vars = ["SILVER_ACCESS_KEY", "SILVER_SECRET_KEY", "SILVER_BUCKET"]
            else:
                access_key = os.getenv("GOLD_ACCESS_KEY")
                secret_key = os.getenv("GOLD_SECRET_KEY")
                self.bucket = os.getenv("GOLD_BUCKET")
                required_vars = ["GOLD_ACCESS_KEY", "GOLD_SECRET_KEY", "GOLD_BUCKET"]
            endpoint = os.getenv("S3_ENDPOINT", "http://localhost:9000")

        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise EnvironmentError(
                f"Missing required environment variables for {self.tier} tier: {', '.join(missing_vars)}"
            )

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
        
        self.endpoint = endpoint

    def _key(self, path: str) -> str:
        return str(PurePosixPath(path).as_posix().lstrip("/"))
    
    def _get_absolute_url(self, path: str) -> str:
        key = self._key(path)
        return f"{self.endpoint}/{self.bucket}/{key}"

    def put_file(self, local_path: str, path: str) -> None:
        absolute_url = self._get_absolute_url(path)
        try:
            self.client.upload_file(local_path, self.bucket, self._key(path))
            print(f"🔗 File uploaded to: {absolute_url}")
        except EndpointConnectionError:
            print(f"❌ Cannot connect to storage at {self.endpoint}", file=sys.stderr)
            sys.exit(1)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'AccessDenied':
                print(f"❌ Access denied when uploading to: {path}", file=sys.stderr)
            elif error_code == 'NoSuchBucket':
                print(f"❌ Bucket not found: {self.bucket}", file=sys.stderr)
            else:
                print(f"❌ Storage error when uploading to: {path}: {e}", file=sys.stderr)
            sys.exit(1)

    def get_file(self, path: str, local_path: str) -> None:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        absolute_url = self._get_absolute_url(path)
        try:
            self.client.download_file(self.bucket, self._key(path), local_path)
            print(f"📥 File downloaded from: {absolute_url}")
        except EndpointConnectionError:
            print(f"❌ Cannot connect to storage at {self.endpoint}", file=sys.stderr)
            sys.exit(1)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchKey':
                print(f"❌ File not found: {path}", file=sys.stderr)
            elif error_code == 'AccessDenied':
                print(f"❌ Access denied when reading: {path}", file=sys.stderr)
            else:
                print(f"❌ Storage error when reading: {path}: {e}", file=sys.stderr)
            sys.exit(1)

    def put_bytes(self, data: bytes, path: str) -> None:
        absolute_url = self._get_absolute_url(path)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._key(path),
                Body=data
            )
            print(f"💾 Data stored at: {absolute_url}")
        except EndpointConnectionError:
            print(f"❌ Cannot connect to storage at {self.endpoint}", file=sys.stderr)
            sys.exit(1)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'AccessDenied':
                print(f"❌ Access denied when writing to: {path}", file=sys.stderr)
            elif error_code == 'NoSuchBucket':
                print(f"❌ Bucket not found: {self.bucket}", file=sys.stderr)
            else:
                print(f"❌ Storage error when writing to: {path}: {e}", file=sys.stderr)
            sys.exit(1)

    def get_bytes(self, path: str) -> bytes:
        absolute_url = self._get_absolute_url(path)
        try:
            obj = self.client.get_object(
                Bucket=self.bucket,
                Key=self._key(path)
            )
            print(f"📥 Data retrieved from: {absolute_url}")
            return obj["Body"].read()
        except EndpointConnectionError:
            print(f"❌ Cannot connect to storage at {self.endpoint}", file=sys.stderr)
            sys.exit(1)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchKey':
                print(f"❌ File not found: {path}", file=sys.stderr)
            elif error_code == 'AccessDenied':
                print(f"❌ Access denied when reading: {path}", file=sys.stderr)
            else:
                print(f"❌ Storage error when reading: {path}: {e}", file=sys.stderr)
            sys.exit(1)

    def list(self, prefix: str = "") -> List[str]:
        resp = self.client.list_objects_v2(
            Bucket=self.bucket,
            Prefix=self._key(prefix)
        )

        return [
            obj["Key"]
            for obj in resp.get("Contents", [])
        ]

    def list_all(self) -> List[str]:
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys


def get_storage(tier: str = "bronze", workspace: str | None = None) -> Storage:
    """
    .. deprecated::
        Use the ``@lineage_job`` decorator instead, which injects a ``LineageStorage``
        instance via the ``get_storage`` argument. Example::

            from steve_cli.decorators import lineage_job
            from steve_cli.lineage.storage import GetStorage

            @lineage_job()
            def run(get_storage: GetStorage):
                bronze = get_storage("bronze")
                df = bronze.read_csv("path/to/file.csv")
    """
    return S3Storage(tier=tier, workspace=workspace)