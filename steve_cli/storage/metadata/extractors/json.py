from __future__ import annotations

import json

from steve_cli.storage.metadata.port import ColumnMetadata, FileMetadata, MetadataExtractorPort


class JsonExtractor(MetadataExtractorPort):
    extensions = (".json", ".jsonl", ".ndjson")

    def extract(self, data: bytes, path: str) -> FileMetadata:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")

        if path.endswith((".jsonl", ".ndjson")):
            rows_raw = [json.loads(l) for l in text.splitlines() if l.strip()]
            rows = len(rows_raw)
            sample = rows_raw[0] if rows_raw else {}
        else:
            obj = json.loads(text)
            if isinstance(obj, list):
                rows = len(obj)
                sample = obj[0] if obj else {}
            else:
                rows = 1
                sample = obj

        columns = []
        if isinstance(sample, dict):
            columns = [
                ColumnMetadata(name=k, type=type(v).__name__)
                for k, v in sample.items()
            ]

        return FileMetadata(
            format="json",
            size_bytes=len(data),
            rows=rows,
            columns=columns,
        )
