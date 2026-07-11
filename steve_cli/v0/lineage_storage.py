from typing import List


class LineageStorage(Storage):

    def __init__(
        self,
        storage: Storage,
        lineage: LineageCollector,
    ):
        self.storage = storage
        self.lineage = lineage


    def put_file(
        self,
        local_path: str,
        path: str,
    ):
        self.storage.put_file(local_path, path)

        self.lineage.record_write(path)


    def get_file(
        self,
        path: str,
        local_path: str,
    ):
        self.storage.get_file(path, local_path)

        self.lineage.record_read(path)


    def put_bytes(
        self,
        data: bytes,
        path: str,
    ):
        self.storage.put_bytes(data, path)

        self.lineage.record_write(path)


    def get_bytes(
        self,
        path: str,
    ) -> bytes:

        data = self.storage.get_bytes(path)

        self.lineage.record_read(path)

        return data


    def list(
        self,
        prefix: str = "",
    ) -> List[str]:

        return self.storage.list(prefix)