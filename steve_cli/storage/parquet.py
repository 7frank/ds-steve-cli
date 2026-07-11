from steve_cli.storage.metadata.port import ColumnMetadata, FileMetadata
from steve_cli.storage.metadata.extractors.parquet import ParquetExtractor

ParquetMetadata = FileMetadata


def extract_parquet_metadata(data: bytes) -> FileMetadata:
    return ParquetExtractor().extract(data, "file.parquet")


__all__ = ["ParquetMetadata", "ColumnMetadata", "extract_parquet_metadata"]
