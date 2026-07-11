from __future__ import annotations

import os
from typing import Any, Dict, Type

from .port import LineagePort


class LineageRegistry:
    _registry: Dict[str, Type[LineagePort]] = {}

    @classmethod
    def register(cls, name: str, adapter_class: Type[LineagePort]) -> None:
        cls._registry[name] = adapter_class

    @classmethod
    def get(cls, name: str) -> Type[LineagePort]:
        if name not in cls._registry:
            raise KeyError(
                f"Lineage provider '{name}' is not registered. "
                f"Available: {list(cls._registry.keys())}"
            )
        return cls._registry[name]

    @classmethod
    def create(cls, name: str | None = None, **kwargs: Any) -> LineagePort:
        provider = name or os.getenv("LINEAGE_PROVIDER", "openlineage")
        return cls.get(provider)(**kwargs)

    @classmethod
    def list(cls) -> list[str]:
        return list(cls._registry.keys())


def _register_defaults() -> None:
    from .adapters.null import NullLineageAdapter
    from .adapters.logging import LoggingLineageAdapter

    LineageRegistry.register("null", NullLineageAdapter)
    LineageRegistry.register("logging", LoggingLineageAdapter)

    try:
        from .adapters.openlineage import OpenLineageAdapter

        LineageRegistry.register("openlineage", OpenLineageAdapter)
    except ImportError:
        LineageRegistry.register("openlineage", NullLineageAdapter)


_register_defaults()
