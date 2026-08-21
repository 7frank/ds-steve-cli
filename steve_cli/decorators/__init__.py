from collections.abc import Callable

from steve_cli.lineage.storage import LineageStorage

from .lineage_job import lineage_job

GetTables = Callable[[str, str | None], LineageStorage]
GetStorage = Callable[[str, str | None], LineageStorage]

__all__ = ["GetStorage", "GetTables", "lineage_job"]
