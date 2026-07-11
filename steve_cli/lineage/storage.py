from __future__ import annotations

from typing import Callable, List, Union

from steve_cli.storage.protocol import Storage


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

    def put_file(self, local_path: str, path: str) -> None:
        self._storage.put_file(local_path, path)
        self._session.record_write(path)

    def get_file(self, path: str, local_path: str) -> None:
        self._storage.get_file(path, local_path)
        self._session.record_read(path)

    def put_bytes(self, data: bytes, path: str) -> None:
        self._storage.put_bytes(data, path)
        self._session.record_write(path)

    def get_bytes(self, path: str) -> bytes:
        data = self._storage.get_bytes(path)
        self._session.record_read(path)
        return data

    def list(self, prefix: str = "") -> List[str]:
        return self._storage.list(prefix)

    def list_all(self) -> List[str]:
        return self._storage.list_all()
