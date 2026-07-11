from __future__ import annotations

import json
import logging

from steve_cli.lineage.port import LineageEvent, LineagePort

logger = logging.getLogger("steve_cli.lineage")


class LoggingLineageAdapter(LineagePort):
    def emit(self, event: LineageEvent) -> None:
        logger.info(
            "lineage event",
            extra={
                "lineage": {
                    "state": event.state,
                    "job": event.job_name,
                    "namespace": event.namespace,
                    "run_id": event.run_id,
                    "inputs": [{"namespace": d.namespace, "name": d.name} for d in event.inputs],
                    "outputs": [{"namespace": d.namespace, "name": d.name} for d in event.outputs],
                }
            },
        )
