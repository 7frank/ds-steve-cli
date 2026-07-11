from __future__ import annotations

from steve_cli.lineage.port import LineageEvent, LineagePort


class NullLineageAdapter(LineagePort):
    def emit(self, event: LineageEvent) -> None:
        pass
