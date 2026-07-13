from __future__ import annotations

import functools
import inspect
from pathlib import Path
from typing import Any, Callable

from steve_cli.lineage.collector import make_session
from steve_cli.storage.s3 import S3Storage
from steve_cli.lineage.storage import LineageStorage


def lineage_job(
    name: str | None = None,
    namespace: str | None = None,
    lineage_provider: str | None = None,
    lineage_enabled: bool = True,
) -> Callable:
    def decorator(fn: Callable) -> Callable:
        if name:
            job_name = name
        else:
            caller_file = inspect.getfile(fn)
            stem = Path(caller_file).stem
            job_name = f"{stem}.{fn.__name__}"

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

            try:
                result = fn(*args, get_storage=get_storage, **kwargs)
            except Exception as exc:
                session.fail(exc)
                from steve_cli.validation.port import DataQualityError
                if isinstance(exc, DataQualityError):
                    import sys
                    print(f"ERROR {exc}", file=sys.stderr)
                    sys.exit(1)
                raise

            session.complete()
            return result

        if fn.__module__ == "__main__":
            import logging
            import os
            logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
            wrapper()

        return wrapper

    return decorator
