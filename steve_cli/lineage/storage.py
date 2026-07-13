from __future__ import annotations

import logging
from io import BytesIO
from typing import Any, Callable, List, Union

from steve_cli.storage.protocol import Storage

GetStorage = Callable[..., "LineageStorage"]

logger = logging.getLogger(__name__)


class DatasetHandle:
    def __init__(self, path: str, data: bytes, session: Any, is_read: bool = False):
        self.data = data
        self._path = path
        self._session = session
        self._is_read = is_read

    def describe(self, **column_descriptions: str) -> DatasetHandle:
        existing_fields = self._session._facets.get(self._path, {}).get("schema", {}).get("fields", [])
        by_name = {f["name"]: dict(f) for f in existing_fields}
        for col, desc in column_descriptions.items():
            by_name.setdefault(col, {"name": col, "type": "string"})["description"] = desc
        self._session.attach_facets(self._path, {"schema": {"fields": list(by_name.values())}})
        return self

    def validate(self, result: Any) -> DatasetHandle:
        self._session.attach_validation(self._path, result)
        if self._is_read:
            result.raise_on_errors()
        return self


def _extract_facets(data: bytes, path: str) -> dict:
    try:
        from steve_cli.storage.metadata.registry import MetadataRegistry
        meta = MetadataRegistry.extract(data, path)
        return meta.to_openlineage_facet().get("datasetFacets", {})
    except Exception as exc:
        logger.debug("Could not extract metadata facets for %s: %s", path, exc)
        return {}


class LineageStorage:
    def __init__(self, storage: Union[Storage, Callable[[], Storage]], session: object):
        self._storage_or_factory = storage
        self._session = session
        self.__resolved: Storage | None = None

    @property
    def _storage(self) -> Storage:
        if self.__resolved is None:
            if callable(self._storage_or_factory) and not hasattr(self._storage_or_factory, 'put_bytes'):
                self.__resolved = self._storage_or_factory()
            else:
                self.__resolved = self._storage_or_factory  # type: ignore[assignment]
        return self.__resolved

    @property
    def _dataset_namespace(self) -> str | None:
        s = self._storage
        if hasattr(s, 'endpoint') and hasattr(s, 'bucket'):
            return f"{s.endpoint}/{s.bucket}"
        return None

    def put_file(self, local_path: str, path: str) -> None:
        self._storage.put_file(local_path, path)
        self._session.record_write(path, namespace=self._dataset_namespace)

    def get_file(self, path: str, local_path: str) -> None:
        self._storage.get_file(path, local_path)
        self._session.record_read(path, namespace=self._dataset_namespace)

    def put_bytes(self, data: bytes, path: str) -> None:
        self._storage.put_bytes(data, path)
        facets = _extract_facets(data, path)
        self._session.record_write(path, facets or None, namespace=self._dataset_namespace)

    def get_bytes(self, path: str) -> bytes:
        data = self._storage.get_bytes(path)
        facets = _extract_facets(data, path)
        self._session.record_read(path, facets or None, namespace=self._dataset_namespace)
        return data

    def read(self, path: str) -> DatasetHandle:
        return DatasetHandle(path, self.get_bytes(path), self._session, is_read=True)

    def write(self, path: str, data: bytes) -> DatasetHandle:
        self.put_bytes(data, path)
        return DatasetHandle(path, data, self._session)

    def read_df(self, path: str, parser: Callable, validator: Any = None) -> Any:
        data = self.get_bytes(path)
        handle = DatasetHandle(path, data, self._session, is_read=True)
        df = parser(BytesIO(data))
        if validator is not None:
            result = validator.validate(df)
            handle.validate(result)
        return df

    def read_csv(self, path: str, validator: Any = None, **kwargs: Any) -> Any:
        import polars as pl
        return self.read_df(path, lambda b: pl.read_csv(b, **kwargs), validator)

    def read_parquet(self, path: str, validator: Any = None, **kwargs: Any) -> Any:
        import polars as pl
        return self.read_df(path, lambda b: pl.read_parquet(b, **kwargs), validator)

    def read_excel(self, path: str, validator: Any = None, **kwargs: Any) -> Any:
        import polars as pl
        return self.read_df(path, lambda b: pl.read_excel(b, **kwargs), validator)

    def write_df(self, path: str, df: Any, serializer: Callable) -> DatasetHandle:
        data = serializer(df)
        self.put_bytes(data, path)
        return DatasetHandle(path, data, self._session)

    def write_csv(self, path: str, df: Any, **kwargs: Any) -> DatasetHandle:
        return self.write_df(path, df, lambda d: d.write_csv(**kwargs).encode())

    def write_parquet(self, path: str, df: Any, **kwargs: Any) -> DatasetHandle:
        def _to_bytes(d: Any) -> bytes:
            buf = BytesIO()
            d.write_parquet(buf, **kwargs)
            return buf.getvalue()
        return self.write_df(path, df, _to_bytes)

    def attach_facets(self, path: str, facets: dict) -> None:
        self._session.attach_facets(path, facets)

    def attach_validation(self, path: str, result: object) -> None:
        self._session.attach_validation(path, result)

    def list(self, prefix: str = "") -> List[str]:
        return self._storage.list(prefix)

    def list_all(self) -> List[str]:
        return self._storage.list_all()
