from __future__ import annotations

from steve_cli.lineage.port import LineageEvent, LineagePort


class CompositeLineageAdapter(LineagePort):
    def __init__(self, adapters: list[LineagePort]):
        self._adapters = adapters

    def emit(self, event: LineageEvent) -> None:
        for adapter in self._adapters:
            adapter.emit(event)
