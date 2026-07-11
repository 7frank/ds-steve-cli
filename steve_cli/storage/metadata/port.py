from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ColumnMetadata:
    name: str
    type: str


@dataclass
class FileMetadata:
    format: str
    size_bytes: int = 0
    rows: int = 0
    columns: List[ColumnMetadata] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": self.format,
            "size_bytes": self.size_bytes,
            "rows": self.rows,
            "columns": [{"name": c.name, "type": c.type} for c in self.columns],
            **self.extra,
        }

    def to_openlineage_facet(self) -> Dict[str, Any]:
        facets: Dict[str, Any] = {
            "storage": {
                "_producer": "steve-cli",
                "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/StorageDatasetFacet.json",
                "storageLayer": "s3",
                "fileFormat": self.format,
            },
        }
        if self.columns:
            facets["schema"] = {
                "_producer": "steve-cli",
                "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/SchemaDatasetFacet.json",
                "fields": [{"name": c.name, "type": c.type} for c in self.columns],
            }
        if self.extra:
            facets[self.format] = {"_producer": "steve-cli", **self.extra}
        return {"datasetFacets": facets}


class MetadataExtractorPort(ABC):
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def extract(self, data: bytes, path: str) -> FileMetadata: ...
