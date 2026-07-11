from __future__ import annotations

import functools
import os
from typing import Any, Callable

from steve_cli.lineage.collector import make_session
from steve_cli.storage.s3 import S3Storage
from steve_cli.lineage.storage import LineageStorage
from steve_cli.validation.registry import ValidationRegistry


def lineage_job(
    name: str | None = None,
    namespace: str | None = None,
    validation: str | None = None,
    lineage_provider: str | None = None,
    lineage_enabled: bool = True,
) -> Callable:
    def decorator(fn: Callable) -> Callable:
        job_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            session = make_session(
                namespace=namespace,
                job_name=job_name,
                provider=lineage_provider,
                enabled=lineage_enabled,
            )

            session.start()

            def get_storage(tier: str = "bronze", workspace: str | None = None) -> LineageStorage:
                return LineageStorage(
                    storage=lambda: S3Storage(tier=tier, workspace=workspace),
                    session=session,
                )

            provider = validation or os.getenv("VALIDATION_PROVIDER", "null")
            validator = ValidationRegistry.create(provider)

            try:
                result = fn(*args, get_storage=get_storage, validator=validator, **kwargs)
            except Exception as exc:
                session.fail(exc)
                raise

            session.complete()
            return result

        return wrapper

    return decorator
