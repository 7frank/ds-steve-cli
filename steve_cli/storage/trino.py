from __future__ import annotations

import io
import logging
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from .branch import get_branch_prefix

logger = logging.getLogger(__name__)


def _derive_schema(tier: str, workspace: str | None, branch: str) -> str:
    base = f"{workspace.lower().replace('-', '_')}_{tier.lower()}" if workspace else tier.lower()
    return f"{base}__{branch}"


class TrinoStorage:
    def __init__(self, tier: str = "bronze", workspace: str | None = None):
        from steve_cli.auth import get_service_url
        self._branch = get_branch_prefix()
        self._tier = tier.lower()
        self._workspace_id = os.getenv("WORKSPACE_ID")
        trino_endpoint = get_service_url("trino", self._workspace_id)
        if not trino_endpoint:
            from steve_cli.auth import _get_workspace_entry
            entry = _get_workspace_entry(self._workspace_id)
            checks = [
                ("✓" if entry else "✗", "credentials found in ~/.steve/credentials.json"),
                ("✓" if entry.get("token") else "✗", "token present"),
                ("✓" if entry.get("base_url") else "✗", "base_url present (STEVE_BASE_URL configured on automation-kernel)"),
                ("✓" if self._workspace_id else "✗", f"WORKSPACE_ID env var set (got: {self._workspace_id!r})"),
            ]
            detail = "\n".join(f"  {mark} {desc}" for mark, desc in checks)
            raise OSError(f"Trino endpoint not found:\n{detail}")
        resolved_workspace = workspace or os.getenv("WORKSPACE_NAME")
        default_catalog = f"{resolved_workspace}-{tier}" if resolved_workspace else "minio"
        self.catalog = os.getenv("TRINO_CATALOG", default_catalog)
        self.schema = os.environ.get("TRINO_SCHEMA") or _derive_schema(tier, resolved_workspace, self._branch)
        self.user = os.getenv("TRINO_USER") or resolved_workspace or "admin"
        self._base = trino_endpoint.rstrip("/")
        self._lakekeeper_endpoint = os.getenv("LAKEKEEPER_ENDPOINT") or get_service_url("lakekeeper", self._workspace_id)
        self._lakekeeper_warehouse = os.getenv("LAKEKEEPER_WAREHOUSE", default_catalog)
        _iceberg_bucket = os.getenv(f"{tier.upper()}_BUCKET", "")
        if _iceberg_bucket:
            self._iceberg_location_prefix = f"s3://{_iceberg_bucket}/_iceberg/{self._branch}"
        else:
            self._iceberg_location_prefix = f"_iceberg/{self._branch}"
        self.__iceberg_catalog = None

    @property
    def _iceberg_catalog(self):
        if self.__iceberg_catalog is None:
            from pyiceberg.catalog.rest import RestCatalog
            from pyiceberg.io import load_file_io
            from pyiceberg.table import Table

            # WORKAROUND: Lakekeeper's remote S3 presigner is bypassed for external access.
            #
            # Lakekeeper returns `s3.signer` / `s3.signer.uri` in the table config, which
            # pyiceberg uses to sign S3 requests via Lakekeeper's signing endpoint. This works
            # inside the cluster but fails from a laptop because:
            #   1. The signed URI Lakekeeper produces contains the cluster-internal hostname
            #      (e.g. http://minio:9000) which is unreachable externally.
            #   2. Even after overriding the signer URI to the public lakekeeper subdomain,
            #      Lakekeeper's signer returns HTTP 500 for URIs with the public minio hostname
            #      because it only knows the internal minio endpoint.
            #
            # Current workaround: strip all signer config from the merged table properties and
            # use direct S3 credentials (access-key + secret-key) with the public minio endpoint
            # from ~/.steve/credentials.json. This bypasses Lakekeeper's access control for S3
            # writes — credentials live on disk rather than being vended by the cluster.
            #
            # To revert: configure Lakekeeper with its public S3 endpoint so presigned URIs
            # contain a resolvable hostname, then remove the merged.pop() calls below and
            # restore `s3.signer.uri: lakekeeper_base` in s3_overrides.
            tier = self._tier.upper()
            _lk = self._lakekeeper_endpoint or "http://lakekeeper:8181"
            lakekeeper_base = _lk.rstrip("/") + "/catalog"
            from steve_cli.auth import get_s3_endpoint as _get_s3_ep
            _s3_ep = _get_s3_ep(self._workspace_id) or os.getenv("S3_ENDPOINT", "http://minio:9000")
            s3_overrides = {
                "s3.endpoint": _s3_ep,
                "s3.access-key-id": os.getenv(f"{tier}_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID", ""),
                "s3.secret-access-key": os.getenv(f"{tier}_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", ""),
                "s3.path-style-access": "true",
            } if self._lakekeeper_endpoint else {}

            class _PatchedRestCatalog(RestCatalog):
                def _response_to_table(self_, identifier_tuple, table_response):
                    merged = {**table_response.metadata.properties, **table_response.config, **s3_overrides, "uri": lakekeeper_base}
                    # Strip remote-signer config — see WORKAROUND comment above.
                    merged.pop("s3.signer", None)
                    merged.pop("s3.signer.uri", None)
                    merged.pop("s3.signer.endpoint", None)
                    return Table(
                        identifier=identifier_tuple,
                        metadata_location=table_response.metadata_location,
                        metadata=table_response.metadata,
                        io=load_file_io(merged, table_response.metadata_location),
                        catalog=self_,
                        config=table_response.config,
                    )

            from steve_cli.auth import get_token
            stp_token = get_token(self._workspace_id)
            token_kwargs = {"token": stp_token} if stp_token else {}
            try:
                catalog = _PatchedRestCatalog(
                    "lakekeeper",
                    uri=lakekeeper_base,
                    warehouse=self._lakekeeper_warehouse,
                    **s3_overrides,
                    **token_kwargs,
                )
            except Exception as exc:
                msg = str(exc)
                if "NoSuchWarehouseException" in msg or "does not exist" in msg:
                    raise RuntimeError(
                        f"Lakekeeper warehouse '{self._lakekeeper_warehouse}' not found at {lakekeeper_base}.\n"
                        f"The workspace may not have been provisioned yet — ensure the workspace was created in the data mesh before running this job."
                    ) from None
                raise
            if self._lakekeeper_endpoint:
                catalog.uri = lakekeeper_base
            self.__iceberg_catalog = catalog
        return self.__iceberg_catalog

    def _execute(self, sql: str) -> list[dict]:
        from steve_cli.auth import get_token
        trace = os.getenv("STEVE_TRINO_TRACE") == "1"
        headers = {
            "X-Trino-User": self.user,
            "X-Trino-Catalog": self.catalog,
            "X-Trino-Schema": self.schema,
        }
        token = get_token(self._workspace_id)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        t0 = time.perf_counter()
        try:
            resp = requests.post(f"{self._base}/v1/statement", data=sql, headers=headers)
        except requests.exceptions.ConnectionError as exc:
            raise OSError(
                f"TrinoStorage: cannot connect to Trino at {self._base} — "
                f"check WORKSPACE_ID env var and credentials in ~/.steve/credentials.json"
            ) from exc
        if not resp.ok:
            raise OSError(
                f"TrinoStorage: POST /v1/statement failed with HTTP {resp.status_code} "
                f"[url={self._base}] — auth proxy may not be routing to Trino, "
                f"or the workspace is not accessible (sql: {sql[:100]!r})"
            )
        if trace:
            logger.warning("[trino] POST /v1/statement: %.3fs", time.perf_counter() - t0)

        data = resp.json()
        rows: list[dict] = []
        poll = 0
        while True:
            if "data" in data and "columns" in data:
                col_names = [c["name"] for c in data["columns"]]
                for row in data["data"]:
                    rows.append(dict(zip(col_names, row)))
            next_uri = data.get("nextUri")
            if not next_uri:
                break
            time.sleep(0.1)
            next_uri = next_uri.replace("http://trino:8080", self._base).replace("https://trino:8080", self._base)
            t_poll = time.perf_counter()
            try:
                resp = requests.get(next_uri, headers=headers)
            except requests.exceptions.ConnectionError as exc:
                raise OSError(
                    f"TrinoStorage: connection lost while polling Trino result [url={next_uri}] — "
                    f"sql: {sql[:100]!r}"
                ) from exc
            if not resp.ok:
                raise OSError(
                    f"TrinoStorage: result poll failed with HTTP {resp.status_code} "
                    f"[url={next_uri}] — Trino rejected query (sql: {sql[:100]!r})"
                )
            poll += 1
            if trace:
                logger.warning("[trino] poll #%d: %.3fs (total %.3fs, %d rows so far)",
                               poll, time.perf_counter() - t_poll, time.perf_counter() - t0, len(rows))
            data = resp.json()

        if trace:
            logger.warning("[trino] query done: %.3fs total, %d rows, %d polls", time.perf_counter() - t0, len(rows), poll)
        return rows

    @staticmethod
    def _table_name(path: str) -> str:
        return path.strip("/").replace("/", "_").replace(".", "_")

    def list(self, prefix: str = "") -> list[str]:
        rows = self._execute(f'SHOW TABLES FROM "{self.catalog}".{self.schema}')
        tables = [r["Table"] for r in rows]
        if prefix:
            clean = prefix.strip("/")
            tables = [t for t in tables if t.startswith(clean)]
        return tables

    def list_all(self) -> list[str]:
        return self.list()

    def get_bytes(self, path: str) -> bytes:
        table_name = self._table_name(path)
        table_ref = f'"{self.catalog}".{self.schema}.{table_name}'
        if os.getenv("STEVE_TRINO_TRACE") == "1":
            logger.warning("[trino] get_bytes: %s  (warehouse=%s)", table_ref, self._lakekeeper_warehouse)
        try:
            rows = self._execute(f"SELECT * FROM {table_ref}")
        except OSError:
            raise
        except Exception as exc:
            raise OSError(
                f"TrinoStorage: read from '{table_ref}' failed — {type(exc).__name__}: {exc}"
            ) from exc
        if not rows:
            list_err: str | None = None
            table_names: list[str] | None = None
            try:
                existing = self._execute(f'SHOW TABLES FROM "{self.catalog}".{self.schema}')
                table_names = [r.get("Table", "") for r in existing]
            except Exception as list_exc:
                list_err = str(list_exc)

            if table_names is not None and table_name in table_names:
                raise OSError(
                    f"TrinoStorage: table '{table_ref}' exists in Trino but returned 0 rows — "
                    f"data may not have been written yet, or the last write produced an empty result."
                )
            elif list_err is not None:
                raise OSError(
                    f"TrinoStorage: table '{table_name}' not found in catalog='{self.catalog}' schema='{self.schema}'.\n"
                    f"  Looked in: {table_ref}\n"
                    f"  Could not list tables in that schema: {list_err}\n"
                    f"  Hint: if data was written with get_storage() (S3/Parquet), read it back with get_storage(), not get_tables()."
                )
            elif table_names == []:
                raise OSError(
                    f"TrinoStorage: table '{table_name}' not found — schema '{self.catalog}'.'{self.schema}' exists but contains no tables.\n"
                    f"  Looked in: {table_ref}\n"
                    f"  Hint: if data was written with get_storage() (S3/Parquet), read it back with get_storage(), not get_tables()."
                )
            else:
                raise OSError(
                    f"TrinoStorage: table '{table_name}' not found in catalog='{self.catalog}' schema='{self.schema}'.\n"
                    f"  Looked in: {table_ref}\n"
                    f"  Tables found in that schema: {', '.join(table_names or [])}\n"
                    f"  Hint: if data was written with get_storage() (S3/Parquet), read it back with get_storage(), not get_tables()."
                )
        arrow_table = pa.Table.from_pylist(rows)
        buf = io.BytesIO()
        pq.write_table(arrow_table, buf)
        return buf.getvalue()

    def get_file(self, path: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(self.get_bytes(path))

    def _ensure_namespace(self) -> None:
        import datetime

        from pyiceberg.exceptions import NamespaceAlreadyExistsError
        _lk = self._lakekeeper_endpoint or "http://lakekeeper:8181"
        lakekeeper_base = _lk.rstrip("/") + "/catalog"
        namespace_props = {"location": self._iceberg_location_prefix}
        created = False
        try:
            self._iceberg_catalog.create_namespace(self.schema, properties=namespace_props)
            logger.info("Created namespace %s", self.schema)
            created = True
        except NamespaceAlreadyExistsError:
            pass
        except Exception as exc:
            msg = str(exc)
            if "403" in msg or "Access Denied" in msg or "Forbidden" in msg:
                raise OSError(
                    f"TrinoStorage: namespace '{self.schema}' creation denied at Lakekeeper "
                    f"[{lakekeeper_base}] — check that the token has write access to warehouse "
                    f"'{self._lakekeeper_warehouse}'"
                ) from exc
            raise OSError(
                f"TrinoStorage: namespace '{self.schema}' creation failed at Lakekeeper "
                f"[{lakekeeper_base}] — {type(exc).__name__}: {exc}"
            ) from exc

        if created:
            from pyiceberg.schema import Schema
            from pyiceberg.types import NestedField, StringType
            meta_schema = Schema(
                NestedField(1, "branch", StringType(), required=False),
                NestedField(2, "tier", StringType(), required=False),
                NestedField(3, "schema_name", StringType(), required=False),
                NestedField(4, "iceberg_location", StringType(), required=False),
                NestedField(5, "created_at", StringType(), required=False),
            )
            meta_table_id = f"{self.schema}._meta"
            try:
                meta_table = self._iceberg_catalog.create_table(meta_table_id, schema=meta_schema)
                meta_table.append(pa.table({
                    "branch": [self._branch],
                    "tier": [self._tier],
                    "schema_name": [self.schema],
                    "iceberg_location": [self._iceberg_location_prefix],
                    "created_at": [datetime.datetime.now(datetime.timezone.utc).isoformat()],
                }))
                logger.info("Created _meta table in namespace %s", self.schema)
            except Exception as exc:
                logger.warning("Could not create _meta table in %s: %s", self.schema, exc)

        try:
            self._execute(
                f'CREATE SCHEMA IF NOT EXISTS "{self.catalog}".{self.schema} '
                f"WITH (location = '{self._iceberg_location_prefix}')"
            )
            logger.debug("Ensured Trino schema %s.%s", self.catalog, self.schema)
        except Exception as exc:
            logger.warning("Could not ensure Trino schema %s.%s: %s", self.catalog, self.schema, exc)

    def put_bytes(self, data: bytes, path: str) -> None:
        from pyiceberg.exceptions import NoSuchTableError

        arrow_table = pq.read_table(io.BytesIO(data))
        table_id = f"{self.schema}.{self._table_name(path)}"
        catalog = self._iceberg_catalog
        lakekeeper_base = self._lakekeeper_endpoint or "http://lakekeeper:8181/catalog"

        self._ensure_namespace()

        def _append(iceberg_table, arrow_table, table_id):
            try:
                iceberg_table.append(arrow_table)
            except Exception as exc:
                msg = str(exc)
                if "403" in msg or "Access Denied" in msg or "Forbidden" in msg:
                    raise OSError(
                        f"TrinoStorage: write to '{table_id}' denied via Lakekeeper [{lakekeeper_base}] — "
                        f"Access Denied on warehouse '{self._lakekeeper_warehouse}' "
                        f"(check S3 bucket policy and token permissions)"
                    ) from exc
                raise OSError(
                    f"TrinoStorage: write to '{table_id}' failed via Lakekeeper [{lakekeeper_base}] — "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

        try:
            iceberg_table = catalog.load_table(table_id)
            _append(iceberg_table, arrow_table, table_id)
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
            try:
                iceberg_table = catalog.create_table(table_id, schema=iceberg_schema)
            except Exception as exc:
                msg = str(exc)
                if "403" in msg or "Access Denied" in msg or "Forbidden" in msg:
                    raise OSError(
                        f"TrinoStorage: create table '{table_id}' denied via Lakekeeper [{lakekeeper_base}] — "
                        f"Access Denied on warehouse '{self._lakekeeper_warehouse}' "
                        f"(check S3 bucket policy and token permissions)"
                    ) from exc
                raise OSError(
                    f"TrinoStorage: create table '{table_id}' failed via Lakekeeper [{lakekeeper_base}] — "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            _append(iceberg_table, arrow_table, table_id)

        if os.getenv("STEVE_TRINO_TRACE") == "1":
            snapshot = None
            try:
                snapshot = catalog.load_table(f"{self.schema}.{self._table_name(path)}").current_snapshot()
            except Exception:
                pass
            logger.warning(
                "[trino] put_bytes: wrote %d rows → %s.%s  (warehouse=%s, location=%s%s)",
                len(arrow_table), self.catalog, table_id,
                self._lakekeeper_warehouse, self._iceberg_location_prefix,
                f", snapshot={snapshot.snapshot_id}" if snapshot else "",
            )
        logger.info("Written %d rows to %s", len(arrow_table), table_id)

    def put_file(self, local_path: str, path: str) -> None:
        self.put_bytes(Path(local_path).read_bytes(), path)
