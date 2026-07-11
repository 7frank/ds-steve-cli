from __future__ import annotations

import logging

from steve_cli.lineage.port import DatasetRef, LineageEvent, LineagePort

logger = logging.getLogger(__name__)


class OpenLineageAdapter(LineagePort):
    def __init__(self, url: str):
        try:
            from openlineage.client import OpenLineageClient
            from openlineage.client.transport.http import HttpConfig, HttpTransport
        except ImportError as exc:
            raise ImportError(
                "openlineage-python is required. Install it with: pip install steve-cli[lineage]"
            ) from exc

        # Strip any path suffix — OpenLineageClient expects just the base URL (scheme+host+port)
        from urllib.parse import urlparse
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        transport = HttpTransport(HttpConfig.from_dict({"url": base_url}))
        self._client = OpenLineageClient(transport=transport)

    def emit(self, event: LineageEvent) -> None:
        from openlineage.client.event_v2 import Dataset, Job, Run, RunEvent, RunState
        from openlineage.client.facet import ErrorMessageRunFacet

        run_facets = {}
        if "errorMessage" in event.run_facets:
            err = event.run_facets["errorMessage"]
            run_facets["errorMessage"] = ErrorMessageRunFacet(
                message=err.get("message", ""),
                programmingLanguage=err.get("programmingLanguage", "python"),
            )

        def _to_ol_dataset(ref: DatasetRef) -> Dataset:
            return Dataset(
                namespace=ref.namespace,
                name=ref.name,
                facets=ref.facets or {},
            )

        ol_event = RunEvent(
            eventType=getattr(RunState, event.state),
            eventTime=event.event_time,
            run=Run(runId=event.run_id, facets=run_facets),
            job=Job(namespace=event.namespace, name=event.job_name),
            inputs=[_to_ol_dataset(d) for d in event.inputs],
            outputs=[_to_ol_dataset(d) for d in event.outputs],
        )

        try:
            self._client.emit(ol_event)
        except Exception as exc:
            logger.warning("Failed to emit lineage event to Marquez: %s", exc)
