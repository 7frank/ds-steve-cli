from __future__ import annotations

import os

from steve_cli.storage.metadata.port import FileMetadata, MetadataExtractorPort


class GenericExtractor(MetadataExtractorPort):
    extensions = ()

    def extract(self, data: bytes, path: str) -> FileMetadata:
        ext = os.path.splitext(path)[-1].lower().lstrip(".") or "unknown"

        extra: dict = {}
        try:
            import magic

            extra["mime_type"] = magic.from_buffer(data, mime=True)
        except ImportError:
            pass

        return FileMetadata(
            format=ext,
            size_bytes=len(data),
            rows=0,
            columns=[],
            extra=extra,
        )
