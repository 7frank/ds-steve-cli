from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List
import uuid


@dataclass
class DatasetRef:
    namespace: str
    name: str
    facets: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LineageEvent:
    job_name: str
    namespace: str
    run_id: str
    state: str
    event_time: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    inputs: List[DatasetRef] = field(default_factory=list)
    outputs: List[DatasetRef] = field(default_factory=list)
    run_facets: Dict[str, Any] = field(default_factory=dict)


class LineagePort(ABC):
    @abstractmethod
    def emit(self, event: LineageEvent) -> None: ...
