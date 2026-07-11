from __future__ import annotations

import csv
import io

from steve_cli.storage.metadata.port import ColumnMetadata, FileMetadata, MetadataExtractorPort


class CsvExtractor(MetadataExtractorPort):
    extensions = (".csv", ".tsv", ".txt")

    def extract(self, data: bytes, path: str) -> FileMetadata:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")

        sample = text[:65536]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter

        reader = csv.reader(io.StringIO(text), dialect=dialect)
        rows = list(reader)

        header = rows[0] if rows else []
        data_rows = rows[1:] if len(rows) > 1 else []

        columns = [ColumnMetadata(name=name.strip(), type="string") for name in header]

        return FileMetadata(
            format="csv",
            size_bytes=len(data),
            rows=len(data_rows),
            columns=columns,
            extra={
                "delimiter": repr(delimiter),
                "has_header": bool(header),
            },
        )
