from __future__ import annotations

import io
import logging
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

logger = logging.getLogger(__name__)


def _derive_schema(tier: str, workspace: str | None) -> str | None:
    if workspace:
        prefix = workspace.lower().replace("-", "_")
        return f"ws_{prefix}_{tier.lower()}"
    return None


class TrinoStorage:
    def __init__(self, tier: str = "bronze", workspace: str | None = None):
        trino_endpoint = os.getenv("TRINO_ENDPOINT")
        if not trino_endpoint:
            raise EnvironmentError("TRINO_ENDPOINT is not set — Trino is not available in this environment")
        self.catalog = os.getenv("TRINO_CATALOG", "minio")
        resolved_workspace = workspace or os.getenv("WORKSPACE_NAME")
        self.schema = _derive_schema(tier, resolved_workspace) or os.environ.get("TRINO_SCHEMA", "")
        if not self.schema:
            raise EnvironmentError("TRINO_SCHEMA is not set and no WORKSPACE_NAME or workspace was provided")
        self.user = os.getenv("TRINO_USER", "admin")
        self._base = trino_endpoint.rstrip("/")
        self._lakekeeper_endpoint = os.getenv("LAKEKEEPER_ENDPOINT")
        default_warehouse = f"{resolved_workspace}-{tier}" if resolved_workspace else "minio"
        self._lakekeeper_warehouse = os.getenv("LAKEKEEPER_WAREHOUSE", default_warehouse)
        self.__iceberg_catalog = None

    @property
    def _iceberg_catalog(self):
        if self.__iceberg_catalog is None:
            from pyiceberg.catalog.rest import RestCatalog
            from pyiceberg.io import load_file_io
            from pyiceberg.table import Table

            # FIXME: Interim workaround — pyiceberg cannot use Lakekeeper's remote signing
            # endpoint when running outside the cluster (hostname 'lakekeeper' doesn't resolve
            # locally). We subclass RestCatalog to inject our local signer URI after pyiceberg
            # merges table config (which overwrites s3.signer.uri with the internal hostname).
            # Remove once Increment 5 (STS credential vending) is wired.
            tier = self.schema.rsplit("_", 1)[-1].upper()
            lakekeeper_base = self._lakekeeper_endpoint or "http://lakekeeper:8181/catalog"
            s3_overrides = {
                "s3.endpoint": os.getenv("S3_ENDPOINT", "http://minio:9000"),
                "s3.access-key-id": os.getenv(f"{tier}_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID", ""),
                "s3.secret-access-key": os.getenv(f"{tier}_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", ""),
                "s3.path-style-access": "true",
                "s3.signer.uri": lakekeeper_base,
            } if self._lakekeeper_endpoint else {}

            class _PatchedRestCatalog(RestCatalog):
                def _response_to_table(self_, identifier_tuple, table_response):
                    merged = {**table_response.metadata.properties, **table_response.config, **s3_overrides, "uri": lakekeeper_base}
                    return Table(
                        identifier=identifier_tuple,
                        metadata_location=table_response.metadata_location,
                        metadata=table_response.metadata,
                        io=load_file_io(merged, table_response.metadata_location),
                        catalog=self_,
                        config=table_response.config,
                    )

            catalog = _PatchedRestCatalog(
                "lakekeeper",
                uri=lakekeeper_base,
                warehouse=self._lakekeeper_warehouse,
                **s3_overrides,
            )
            if self._lakekeeper_endpoint:
                catalog.uri = lakekeeper_base
            self.__iceberg_catalog = catalog
        return self.__iceberg_catalog

    def _execute(self, sql: str) -> list[dict]:
        headers = {
            "X-Trino-User": self.user,
            "X-Trino-Catalog": self.catalog,
            "X-Trino-Schema": self.schema,
        }
        resp = requests.post(f"{self._base}/v1/statement", data=sql, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        rows: list[dict] = []
        while True:
            if "data" in data and "columns" in data:
                col_names = [c["name"] for c in data["columns"]]
                for row in data["data"]:
                    rows.append(dict(zip(col_names, row)))
            next_uri = data.get("nextUri")
            if not next_uri:
                break
            time.sleep(0.1)
            resp = requests.get(next_uri, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return rows

    @staticmethod
    def _table_name(path: str) -> str:
        return path.strip("/").replace("/", "_").replace(".", "_")

    def list(self, prefix: str = "") -> list[str]:
        rows = self._execute(f"SHOW TABLES FROM {self.catalog}.{self.schema}")
        tables = [r["Table"] for r in rows]
        if prefix:
            clean = prefix.strip("/")
            tables = [t for t in tables if t.startswith(clean)]
        return tables

    def list_all(self) -> list[str]:
        return self.list()

    def get_bytes(self, path: str) -> bytes:
        rows = self._execute(f"SELECT * FROM {self.catalog}.{self.schema}.{self._table_name(path)}")
        if not rows:
            return b""
        arrow_table = pa.Table.from_pylist(rows)
        buf = io.BytesIO()
        pq.write_table(arrow_table, buf)
        return buf.getvalue()

    def get_file(self, path: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(self.get_bytes(path))

    def _ensure_namespace(self) -> None:
        from pyiceberg.exceptions import NamespaceAlreadyExistsError
        try:
            self._iceberg_catalog.create_namespace(self.schema)
            logger.info("Created namespace %s", self.schema)
        except NamespaceAlreadyExistsError:
            pass

    def put_bytes(self, data: bytes, path: str) -> None:
        from pyiceberg.exceptions import NoSuchTableError

        arrow_table = pq.read_table(io.BytesIO(data))
        table_id = f"{self.schema}.{self._table_name(path)}"
        catalog = self._iceberg_catalog

        self._ensure_namespace()

        try:
            iceberg_table = catalog.load_table(table_id)
            iceberg_table.append(arrow_table)
        except NoSuchTableError:
            from pyiceberg.schema import Schema
            from pyiceberg.types import (
                BinaryType,
                BooleanType,
                DateType,
                DoubleType,
                FloatType,
                IntegerType,
                LongType,
                NestedField,
                StringType,
                TimestampType,
            )

            _PYARROW_TO_ICEBERG = {
                pa.bool_(): BooleanType(),
                pa.int32(): IntegerType(),
                pa.int64(): LongType(),
                pa.float32(): FloatType(),
                pa.float64(): DoubleType(),
                pa.large_utf8(): StringType(),
                pa.utf8(): StringType(),
                pa.large_binary(): BinaryType(),
                pa.binary(): BinaryType(),
                pa.date32(): DateType(),
                pa.timestamp("us"): TimestampType(),
                pa.timestamp("us", tz="UTC"): TimestampType(),
            }

            fields = []
            for i, field in enumerate(arrow_table.schema):
                iceberg_type = _PYARROW_TO_ICEBERG.get(field.type, StringType())
                fields.append(NestedField(field_id=i + 1, name=field.name, field_type=iceberg_type, required=not field.nullable))

            iceberg_schema = Schema(*fields)
            iceberg_table = catalog.create_table(table_id, schema=iceberg_schema)
            iceberg_table.append(arrow_table)

        logger.info("Written %d rows to %s", len(arrow_table), table_id)

    def put_file(self, local_path: str, path: str) -> None:
        self.put_bytes(Path(local_path).read_bytes(), path)
