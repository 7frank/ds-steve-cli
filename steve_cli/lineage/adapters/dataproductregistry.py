from __future__ import annotations

import logging
import os

import requests

from steve_cli.lineage.port import DatasetRef, LineageEvent, LineagePort

logger = logging.getLogger(__name__)


class RegistryLineageAdapter(LineagePort):
    def __init__(self):
        self._url = os.getenv("REGISTRY_URL", "http://localhost:8765")
        self._token = os.getenv("REGISTRY_TOKEN", "")

    def emit(self, event: LineageEvent) -> None:
        if event.state != "COMPLETE":
            return
        for ds in event.outputs:
            self._register(ds)

    def _register(self, ds: DatasetRef) -> None:
        product = {
            "uri": f"dp://d/{ds.namespace}/{ds.name}",
            "name": ds.name,
            "namespace": ds.namespace,
            "description": "Auto-registered from lineage job",
        }
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            requests.post(
                f"{self._url}/api/v1/register",
                json={"product": product},
                headers=headers,
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Failed to register dataset %s/%s in registry: %s", ds.namespace, ds.name, exc)
