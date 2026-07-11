from __future__ import annotations

import os
from typing import Any, Dict, Type

from .port import ValidationPort


class ValidationRegistry:
    _registry: Dict[str, Type[ValidationPort]] = {}

    @classmethod
    def register(cls, name: str, adapter_class: Type[ValidationPort]) -> None:
        cls._registry[name] = adapter_class

    @classmethod
    def get(cls, name: str) -> Type[ValidationPort]:
        if name not in cls._registry:
            raise KeyError(
                f"Validation provider '{name}' is not registered. "
                f"Available: {list(cls._registry.keys())}"
            )
        return cls._registry[name]

    @classmethod
    def create(cls, name: str | None = None, **kwargs: Any) -> ValidationPort:
        provider = name or os.getenv("VALIDATION_PROVIDER", "null")
        return cls.get(provider)(**kwargs)

    @classmethod
    def list(cls) -> list[str]:
        return list(cls._registry.keys())


def _register_defaults() -> None:
    from .adapters.null import NullValidationAdapter

    ValidationRegistry.register("null", NullValidationAdapter)

    try:
        from .adapters.validoopsie import ValidoopsieAdapter

        ValidationRegistry.register("validoopsie", ValidoopsieAdapter)
    except ImportError:
        pass

    try:
        from .adapters.great_expectations import GreatExpectationsAdapter

        ValidationRegistry.register("great_expectations", GreatExpectationsAdapter)
    except ImportError:
        pass


_register_defaults()
