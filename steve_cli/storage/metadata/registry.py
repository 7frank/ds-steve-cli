from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Dict, Type

from .port import FileMetadata, MetadataExtractorPort


class MetadataRegistry:
    _registry: Dict[str, Type[MetadataExtractorPort]] = {}

    @classmethod
    def register(cls, name: str, extractor_class: Type[MetadataExtractorPort]) -> None:
        cls._registry[name] = extractor_class
        for ext in extractor_class.extensions:
            cls._registry[ext.lstrip(".")] = extractor_class

    @classmethod
    def get(cls, name: str) -> Type[MetadataExtractorPort]:
        if name not in cls._registry:
            raise KeyError(
                f"Metadata extractor '{name}' is not registered. "
                f"Available: {list(cls._registry.keys())}"
            )
        return cls._registry[name]

    @classmethod
    def extract(cls, data: bytes, path: str) -> FileMetadata:
        forced = os.getenv("METADATA_EXTRACTOR")
        if forced:
            return cls.get(forced)().extract(data, path)

        ext = PurePosixPath(path).suffix.lower().lstrip(".")
        if ext and ext in cls._registry:
            return cls._registry[ext]().extract(data, path)

        from .extractors.generic import GenericExtractor

        return GenericExtractor().extract(data, path)

    @classmethod
    def list(cls) -> list[str]:
        return list(cls._registry.keys())


def _register_defaults() -> None:
    from .extractors.generic import GenericExtractor
    from .extractors.csv import CsvExtractor
    from .extractors.json import JsonExtractor

    MetadataRegistry.register("generic", GenericExtractor)
    MetadataRegistry.register("csv", CsvExtractor)
    MetadataRegistry.register("json", JsonExtractor)

    try:
        from .extractors.parquet import ParquetExtractor

        MetadataRegistry.register("parquet", ParquetExtractor)
    except ImportError:
        pass

    try:
        from .extractors.excel import ExcelExtractor

        MetadataRegistry.register("excel", ExcelExtractor)
    except ImportError:
        pass


_register_defaults()
