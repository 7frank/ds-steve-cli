from __future__ import annotations

import io

from steve_cli.storage.metadata.port import ColumnMetadata, FileMetadata, MetadataExtractorPort


class ParquetExtractor(MetadataExtractorPort):
    extensions = (".parquet", ".pq")

    def extract(self, data: bytes, path: str) -> FileMetadata:
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ImportError(
                "pyarrow is required for parquet metadata extraction. "
                "Install it with: pip install steve-cli[polars]"
            ) from exc

        buf = io.BytesIO(data)
        pf = pq.ParquetFile(buf)
        meta = pf.metadata
        schema = pf.schema_arrow

        columns = [
            ColumnMetadata(name=field.name, type=str(field.type))
            for field in schema
        ]

        compression = ""
        if meta.num_row_groups > 0:
            rg = meta.row_group(0)
            if rg.num_columns > 0:
                compression = rg.column(0).compression.lower()

        return FileMetadata(
            format="parquet",
            size_bytes=len(data),
            rows=meta.num_rows,
            columns=columns,
            extra={
                "row_groups": meta.num_row_groups,
                "parquet_version": str(meta.format_version),
                "compression": compression,
                "serialized_size": meta.serialized_size,
            },
        )
