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


def _derive_schema(_tier: str, _workspace: str | None, branch: str) -> str:
    return branch


def _patch_pyiceberg_get_fs_once() -> None:
    """Fix pyiceberg race: per-thread get_fs() causes repeated _s3() unregister+register_last
    cycles, leaving a window where S3V4RestSigner is absent from before-sign.s3 handlers.
    Replace get_fs() with an instance-level shared cache so _s3() runs only once per
    FsspecFileIO instance (from the calling thread), eliminating the re-registration race."""
    logger.warning(
            "Applied pyiceberg get_fs monkey-patch (instance-level cache) to work around "
            "S3V4RestSigner race condition. Remove once upstream fixes are released: "
            "https://github.com/apache/iceberg-python/issues/3625 "
            "https://github.com/apache/iceberg-python/issues/3896"
    )
    
    try:
        import threading as _t
        from pyiceberg.io.fsspec import FsspecFileIO

        _caches: dict[int, dict] = {}
        _lock = _t.Lock()
        _orig = FsspecFileIO.get_fs

        def _get_fs_shared(self, scheme: str, hostname=None):
            fio_id = id(self)
            key = (scheme, hostname)
            cache = _caches.get(fio_id)
            if cache is not None and key in cache:
                return cache[key]
            with _lock:
                if fio_id not in _caches:
                    _caches[fio_id] = {}
                cache = _caches[fio_id]
                if key not in cache:
                    cache[key] = _orig(self, scheme, hostname)
            return cache[key]

        FsspecFileIO.get_fs = _get_fs_shared

    except Exception as exc:
        logger.warning("Could not patch pyiceberg FsspecFileIO.get_fs: %s", exc)


_patch_pyiceberg_get_fs_once()


class TrinoStorage:
    def __init__(self, tier: str = "bronze", workspace: str | None = None):
        # aiobotocore 3.9+ defaults to STREAMING-UNSIGNED-PAYLOAD-TRAILER (flexible checksums)
        # which Lakekeeper's remote signing proxy and MinIO don't support.
        os.environ.setdefault('AWS_REQUEST_CHECKSUM_CALCULATION', 'when_required')
        # Reduce keepalive from 12s to 3s: MinIO closes server-side connections faster than
        # aiobotocore's default, causing stale pooled connections to return bare HTTP 403
        # ("Forbidden", no S3 XML) on reuse. 3s ensures idle connections are evicted before
        # the next put_bytes call reaches the S3 write phase.
        import aiobotocore.httpsession
        aiobotocore.httpsession.DEFAULT_KEEPALIVE_TIMEOUT = 3
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
        _lk_env = os.getenv("LAKEKEEPER_ENDPOINT")
        _lk_svc = get_service_url("lakekeeper", self._workspace_id)
        self._lakekeeper_endpoint = _lk_env.rstrip("/") if _lk_env else (_lk_svc.rstrip("/") + "/catalog" if _lk_svc else None)
        self._lakekeeper_base = (self._lakekeeper_endpoint or "http://lakekeeper:8181/catalog").rstrip("/")
        self._lakekeeper_warehouse = os.getenv("LAKEKEEPER_WAREHOUSE", default_catalog)
        _iceberg_bucket = os.getenv(f"{tier.upper()}_BUCKET", "")
        if _iceberg_bucket:
            self._iceberg_location_prefix: str | None = f"s3://{_iceberg_bucket}/_iceberg/{self._branch}"
        else:
            self._iceberg_location_prefix = None

        if os.getenv("STEVE_DIRECT_S3") == "1":
            if not self._iceberg_location_prefix:
                raise OSError(
                    f"TrinoStorage (STEVE_DIRECT_S3=1): missing bucket configuration for tier '{tier}' — "
                    f"{tier.upper()}_BUCKET is not set.\n"
                    f"  Direct S3 mode requires explicit bucket credentials "
                    f"(run 'uv run steve setup env' or check workspace provisioning)."
                )
            from pyiceberg.catalog.rest import RestCatalog
            from pyiceberg.io import load_file_io
            from pyiceberg.table import Table
            from steve_cli.auth import get_s3_endpoint as _get_s3_ep
            _tier = tier.upper()
            _s3_ep = _get_s3_ep(self._workspace_id) or os.getenv("S3_ENDPOINT", "http://minio:9000")
            _s3_overrides = {
                "s3.endpoint": _s3_ep,
                "s3.access-key-id": os.getenv(f"{_tier}_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID", ""),
                "s3.secret-access-key": os.getenv(f"{_tier}_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", ""),
                "s3.path-style-access": "true",
            } if self._lakekeeper_endpoint else {}
            _base = self._lakekeeper_base

            class _PatchedRestCatalog(RestCatalog):
                def _response_to_table(self_, identifier_tuple, table_response):
                    merged = {**table_response.metadata.properties, **table_response.config, **_s3_overrides, "uri": _base}
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

            self._catalog_cls = _PatchedRestCatalog
            self._extra_catalog_kwargs: dict = {**_s3_overrides}
        else:
            self._catalog_cls = None
            self._extra_catalog_kwargs = {}

        self.__iceberg_catalog = None

    @property
    def _iceberg_catalog(self):
        if self.__iceberg_catalog is None:
            from pyiceberg.catalog.rest import RestCatalog
            from steve_cli.auth import get_token
            stp_token = get_token(self._workspace_id)
            token_kwargs = {"token": stp_token} if stp_token else {}
            catalog_cls = self._catalog_cls or RestCatalog
            try:
                catalog = catalog_cls(
                    "lakekeeper",
                    uri=self._lakekeeper_base,
                    warehouse=self._lakekeeper_warehouse,
                    **self._extra_catalog_kwargs,
                    **token_kwargs,
                )
            except Exception as exc:
                msg = str(exc)
                if "NoSuchWarehouseException" in msg or "does not exist" in msg:
                    raise RuntimeError(
                        f"Lakekeeper warehouse '{self._lakekeeper_warehouse}' not found at {self._lakekeeper_base}.\n"
                        f"The workspace may not have been provisioned yet — ensure the workspace was created in the data mesh before running this job."
                    ) from None
                raise
            if self._catalog_cls and self._lakekeeper_endpoint:
                catalog.uri = self._lakekeeper_base
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
            if data.get("error"):
                err = data["error"]
                raise OSError(
                    f"TrinoStorage: Trino query failed — {err.get('message', err)} "
                    f"(type={err.get('errorType')}, code={err.get('errorCode')}, sql: {sql[:120]!r})"
                )
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
        namespace_props = {"location": self._iceberg_location_prefix} if self._iceberg_location_prefix else {}
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
                    f"[{self._lakekeeper_base}] — check that the token has write access to warehouse "
                    f"'{self._lakekeeper_warehouse}'"
                ) from exc
            raise OSError(
                f"TrinoStorage: namespace '{self.schema}' creation failed at Lakekeeper "
                f"[{self._lakekeeper_base}] — {type(exc).__name__}: {exc}"
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
                    "iceberg_location": [self._iceberg_location_prefix or ""],
                    "created_at": [datetime.datetime.now(datetime.timezone.utc).isoformat()],
                }))
                logger.info("Created _meta table in namespace %s", self.schema)
            except Exception as exc:
                logger.warning("Could not create _meta table in %s: %s", self.schema, exc)

        try:
            _location_clause = f" WITH (location = '{self._iceberg_location_prefix}')" if self._iceberg_location_prefix else ""
            self._execute(
                f'CREATE SCHEMA IF NOT EXISTS "{self.catalog}".{self.schema}{_location_clause}'
            )
            logger.debug("Ensured Trino schema %s.%s", self.catalog, self.schema)
        except Exception as exc:
            logger.warning("Could not ensure Trino schema %s.%s: %s", self.catalog, self.schema, exc)

    def put_bytes(self, data: bytes, path: str) -> None:
        from pyiceberg.exceptions import NoSuchTableError, SignError

        arrow_table = pq.read_table(io.BytesIO(data))
        table_id = f"{self.schema}.{self._table_name(path)}"
        catalog = self._iceberg_catalog
        lakekeeper_base = self._lakekeeper_base

        self._ensure_namespace()

        def _append(iceberg_table, arrow_table, table_id):
            if os.getenv("STEVE_TRINO_TRACE") == "1":
                io_props = getattr(iceberg_table.io, "properties", {})
                logger.warning(
                    "[trino] append: table=%s, location=%s, s3.endpoint=%s, signer.uri=%s",
                    table_id,
                    iceberg_table.location(),
                    io_props.get("s3.endpoint", "n/a"),
                    io_props.get("s3.signer.uri", "n/a"),
                )

            s3_client = None
            if os.getenv("STEVE_TRINO_TRACE") == "1":
                try:
                    import threading as _threading
                    s3_fs = iceberg_table.io._get_fs("s3")
                    s3_client = s3_fs.s3

                    def _trace_sign(request, **kwargs):
                        logger.warning("[trino] s3 SIGN %s req_id=%x (thread=%s)",
                                       request.method, id(request),
                                       _threading.current_thread().name)

                    def _trace_send(request, **kwargs):
                        hdrs = dict(getattr(request, "headers", {}))
                        auth = next((v for k, v in hdrs.items() if k.lower() == "authorization"), "<MISSING>")
                        logger.warning("[trino] s3.%s req_id=%x auth=%s (thread=%s)",
                                       request.method, id(request),
                                       auth[:24] if auth != "<MISSING>" else "<MISSING>",
                                       _threading.current_thread().name)

                    def _trace_retry(response=None, operation=None, request_dict=None, **kwargs):
                        if not response:
                            return
                        status = getattr(response[0], "status_code", "?")
                        if status >= 300:
                            url = (request_dict or {}).get("url", "?")
                            logger.warning("[trino] s3 <- %s %s %s", operation or "?", status, url)

                    s3_client.meta.events.register("before-sign.s3", _trace_sign, unique_id="steve-trino-s3-sign")
                    s3_client.meta.events.register("before-send.s3", _trace_send, unique_id="steve-trino-s3-trace")
                    s3_client.meta.events.register("needs-retry.s3", _trace_retry, unique_id="steve-trino-s3-retry")
                except Exception as hook_exc:
                    logger.warning("[trino] TRACE hook failed: %s", hook_exc)
                    s3_client = None

            try:
                iceberg_table.append(arrow_table)
            except SignError as exc:
                raise OSError(
                    f"TrinoStorage: Lakekeeper refused to sign write for '{table_id}' "
                    f"[signer={lakekeeper_base}, warehouse={self._lakekeeper_warehouse}] — {exc}\n"
                    f"  Hint: the token may lack write permissions on this warehouse."
                ) from exc
            except Exception as exc:
                msg = str(exc)
                if "403" in msg or "Access Denied" in msg or "Forbidden" in msg:
                    raise OSError(
                        f"TrinoStorage: write to '{table_id}' denied by MinIO "
                        f"[location={iceberg_table.location()}, warehouse={self._lakekeeper_warehouse}] — "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                raise OSError(
                    f"TrinoStorage: write to '{table_id}' failed via Lakekeeper [{lakekeeper_base}] — "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            finally:
                if s3_client is not None:
                    for event, uid in (
                        ("before-sign.s3", "steve-trino-s3-sign"),
                        ("before-send.s3", "steve-trino-s3-trace"),
                        ("needs-retry.s3", "steve-trino-s3-retry"),
                    ):
                        try:
                            s3_client.meta.events.unregister(event, unique_id=uid)
                        except Exception:
                            pass

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
            except SignError as exc:
                raise OSError(
                    f"TrinoStorage: Lakekeeper refused to sign table creation for '{table_id}' "
                    f"[signer={lakekeeper_base}, warehouse={self._lakekeeper_warehouse}] — {exc}\n"
                    f"  Hint: the token may lack write permissions on this warehouse."
                ) from exc
            except Exception as exc:
                msg = str(exc)
                if "403" in msg or "Access Denied" in msg or "Forbidden" in msg:
                    raise OSError(
                        f"TrinoStorage: create table '{table_id}' denied by MinIO "
                        f"[location={self._iceberg_location_prefix or '<lakekeeper-managed>'}, warehouse={self._lakekeeper_warehouse}] — "
                        f"{type(exc).__name__}: {exc}"
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
                self._lakekeeper_warehouse, self._iceberg_location_prefix or "<lakekeeper-managed>",
                f", snapshot={snapshot.snapshot_id}" if snapshot else "",
            )
        logger.info("Written %d rows to %s", len(arrow_table), table_id)

    def put_file(self, local_path: str, path: str) -> None:
        self.put_bytes(Path(local_path).read_bytes(), path)
