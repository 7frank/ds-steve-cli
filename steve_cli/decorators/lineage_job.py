from __future__ import annotations

import functools
import inspect
from pathlib import Path
from typing import Any, Callable

from steve_cli.lineage.collector import make_session
from steve_cli.storage.s3 import S3Storage
from steve_cli.storage.trino import TrinoStorage
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
            from dotenv import load_dotenv
            load_dotenv(".env", override=False)
            load_dotenv(".workspaces.env", override=False)

            session = make_session(
                namespace=namespace,
                job_name=job_name,
                provider=lineage_provider,
                enabled=lineage_enabled,
            )

            session.start()

            def get_storage(tier: str = "bronze", workspace: str | None = None) -> LineageStorage:
                ls = LineageStorage(
                    storage=lambda: S3Storage(tier=tier, workspace=workspace),
                    session=session,
                )
                if session.namespace in ("default", "unknown") and ls._dataset_namespace:
                    session.namespace = ls._dataset_namespace
                return ls

            def get_tables(tier: str = "bronze", workspace: str | None = None) -> LineageStorage:
                return LineageStorage(
                    storage=lambda: TrinoStorage(tier=tier, workspace=workspace),
                    session=session,
                )

            sig = inspect.signature(fn)
            available = {"get_storage": get_storage, "get_tables": get_tables}
            injectable = {k: v for k, v in available.items() if k in sig.parameters}

            try:
                result = fn(*args, **injectable, **kwargs)
            except Exception as exc:
                session.fail(exc)
                import sys
                import click
                from steve_cli.validation.port import DataQualityError
                if isinstance(exc, (DataQualityError, EnvironmentError)):
                    click.secho(f"ERROR {exc}", fg="red", bold=True, err=True)
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
