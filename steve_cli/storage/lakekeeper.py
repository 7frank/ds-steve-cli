from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePosixPath

import requests

from .branch import get_branch_prefix
from steve_cli.auth import get_service_url, get_token

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

_SIGNER_SSL_PATCHED = False


def _patch_s3v4_signer_no_ssl_verify() -> None:
    """Replace SIGNERS['S3V4RestSigner'] with a subclass whose requests.Session has verify=False.

    Needed when the Lakekeeper signing endpoint uses a cert with a hostname mismatch.
    Mirrors the pyiceberg get_fs monkey-patch in trino.py — applied once, process-wide.
    """
    global _SIGNER_SSL_PATCHED
    if _SIGNER_SSL_PATCHED:
        return
    try:
        from pyiceberg.io.fsspec import SIGNERS, S3V4RestSigner  # type: ignore[attr-defined]
        orig = SIGNERS.get("S3V4RestSigner", S3V4RestSigner)

        class _NoVerify(orig):  # type: ignore[valid-type]
            def __init__(self, props):  # type: ignore[override]
                super().__init__(props)
                self._session.verify = False

        SIGNERS["S3V4RestSigner"] = _NoVerify
        _SIGNER_SSL_PATCHED = True
        logger.warning(
            "Applied S3V4RestSigner SSL-verify=False patch (LAKEKEEPER_SSL_VERIFY=0). "
            "Remove once the Lakekeeper TLS certificate covers the full hostname."
        )
    except Exception as exc:
        logger.warning("Could not patch S3V4RestSigner SSL verify: %s", exc)


class LakekeeperStorage:
    """
    Storage backend for unstructured/binary files via Lakekeeper Generic Tables (DATASET format).

    This warehouse uses remote signing (s3.signer: S3V4RestSigner) — Lakekeeper signs every
    S3 request rather than vending short-lived credentials. We therefore use pyiceberg's
    FsspecFileIO with S3V4RestSigner, the same mechanism TrinoStorage uses for Iceberg tables.
    """

    def __init__(self, tier: str = "bronze", workspace: str | None = None):
        # aiobotocore 3.9+ defaults to STREAMING-UNSIGNED-PAYLOAD-TRAILER (flexible checksums)
        # which Lakekeeper's remote signing proxy and MinIO don't support.
        os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
        import aiobotocore.httpsession
        aiobotocore.httpsession.DEFAULT_KEEPALIVE_TIMEOUT = 3

        self._tier = tier.lower()
        self._branch = get_branch_prefix()
        self._workspace_id = os.getenv("WORKSPACE_ID")

        _lk_env = os.getenv("LAKEKEEPER_ENDPOINT")
        _lk_svc = get_service_url("lakekeeper", self._workspace_id)
        self._lakekeeper_base_url = (
            _lk_env.rstrip("/") if _lk_env
            else (_lk_svc.rstrip("/") if _lk_svc else "http://lakekeeper:8181")
        )
        self._token = get_token(self._workspace_id)

        resolved_workspace = workspace or os.getenv("WORKSPACE_NAME")
        default_warehouse = f"{resolved_workspace}-{tier}" if resolved_workspace else "minio"
        self._warehouse = os.getenv("LAKEKEEPER_WAREHOUSE", default_warehouse)

        self._namespace = self._branch
        self._table = f"{self._tier}_files"
        self._table_ensured = False
        self.__lk_client = None

    @property
    def _ssl_verify(self) -> bool:
        return os.getenv("LAKEKEEPER_SSL_VERIFY", "1") != "0"

    def _resolve_warehouse_uuid(self) -> str:
        """Resolve warehouse name → UUID (generic-tables path requires a UUID, not a name)."""
        if _UUID_RE.match(self._warehouse):
            return self._warehouse
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            resp = requests.get(
                f"{self._lakekeeper_base_url}/management/v1/warehouse",
                headers=headers,
                verify=self._ssl_verify,
                timeout=10.0,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise OSError(
                f"LakekeeperStorage: could not list warehouses at {self._lakekeeper_base_url} "
                f"to resolve '{self._warehouse}' → UUID — {type(exc).__name__}: {exc}"
            ) from exc
        warehouses = resp.json().get("warehouses", [])
        for w in warehouses:
            if w.get("name") == self._warehouse:
                return w["id"]
        names = [w.get("name") for w in warehouses]
        raise OSError(
            f"LakekeeperStorage: warehouse '{self._warehouse}' not found at "
            f"{self._lakekeeper_base_url} — available: {names}"
        )

    def _get_client(self):
        if self.__lk_client is None:
            import httpx
            from pylakekeeper import Client, StaticToken
            auth = StaticToken(self._token) if self._token else StaticToken("dev")
            warehouse_uuid = self._resolve_warehouse_uuid()
            client = Client(
                base_url=self._lakekeeper_base_url,
                warehouse=warehouse_uuid,
                auth=auth,
            )
            if not self._ssl_verify:
                client._transport._client = httpx.Client(
                    base_url=self._lakekeeper_base_url.rstrip("/"),
                    verify=False,
                    timeout=30.0,
                )
            self.__lk_client = client
        return self.__lk_client

    def _ensure_table(self) -> None:
        if self._table_ensured:
            return
        from pylakekeeper import ConflictError, GenericTableFormat
        c = self._get_client()
        try:
            c.generic_tables.create(
                self._namespace, self._table,
                format=GenericTableFormat.DATASET,
                doc=f"{self._tier} unstructured files ({self._branch})",
            )
            logger.info("Created Lakekeeper generic table %s.%s", self._namespace, self._table)
        except ConflictError:
            pass
        except Exception as exc:
            raise OSError(
                f"LakekeeperStorage: could not create generic table "
                f"'{self._namespace}.{self._table}' at {self._lakekeeper_base_url} "
                f"[warehouse={self._warehouse}] — {type(exc).__name__}: {exc}"
            ) from exc
        self._table_ensured = True

    def _build_io(self, credentials: dict):
        """Build a pyiceberg FsspecFileIO from Lakekeeper remote-signing credentials."""
        if not self._ssl_verify:
            _patch_s3v4_signer_no_ssl_verify()
        from pyiceberg.io import load_file_io
        props = {
            **credentials,
            "token": self._token or "",
            # S3V4RestSigner falls back to properties["uri"] when s3.signer.uri is absent.
            # Python evaluates default arguments eagerly, so "uri" must always be present
            # even when "s3.signer.uri" is set — otherwise dict.get() raises KeyError.
            "uri": (
                credentials.get("s3.signer.uri")
                or credentials.get("signer.uri")
                or self._lakekeeper_base_url.rstrip("/") + "/catalog"
            ),
        }
        return load_file_io(props, "")

    def _vend(self):
        """Return (pyiceberg FileIO, location_prefix) with fresh vended table metadata."""
        self._ensure_table()
        resp = self._get_client().generic_tables.load(
            self._namespace, self._table, vended=True
        )
        return self._build_io(resp.credentials), resp.location.rstrip("/")

    def _full_path(self, location: str, path: str) -> str:
        clean = str(PurePosixPath(path).as_posix().lstrip("/"))
        return f"{location}/{clean}"

    def put_bytes(self, data: bytes, path: str) -> None:
        io, loc = self._vend()
        with io.new_output(self._full_path(loc, path)).create(overwrite=True) as f:
            f.write(data)
        logger.info("Stored %d bytes → %s/%s", len(data), loc, path)

    def get_bytes(self, path: str) -> bytes:
        io, loc = self._vend()
        with io.new_input(self._full_path(loc, path)).open() as f:
            return f.read()

    def put_file(self, local_path: str, path: str) -> None:
        io, loc = self._vend()
        data = Path(local_path).read_bytes()
        with io.new_output(self._full_path(loc, path)).create(overwrite=True) as f:
            f.write(data)
        logger.info("Uploaded %s → %s/%s", local_path, loc, path)

    def get_file(self, path: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        io, loc = self._vend()
        with io.new_input(self._full_path(loc, path)).open() as f:
            Path(local_path).write_bytes(f.read())

    def list(self, prefix: str = "") -> list[str]:
        io, loc = self._vend()
        search = f"{loc}/{prefix.lstrip('/')}" if prefix else loc
        fs = io.get_fs("s3")
        try:
            paths = fs.find(search, detail=False)
        except FileNotFoundError:
            return []
        loc_stripped = loc.removeprefix("s3://")
        result = []
        for p in paths:
            p_stripped = p.removeprefix("s3://")
            if p_stripped.startswith(loc_stripped + "/"):
                result.append(p_stripped[len(loc_stripped) + 1:])
            else:
                result.append(p_stripped)
        return result

    def list_all(self) -> list[str]:
        return self.list()
