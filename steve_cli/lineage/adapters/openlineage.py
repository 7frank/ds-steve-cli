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
        from openlineage.client.event_v2 import Dataset, InputDataset, Job, Run, RunEvent, RunState
        from openlineage.client.facet import (
            Assertion,
            ColumnMetric,
            DataQualityAssertionsDatasetFacet,
            DataQualityMetricsInputDatasetFacet,
            ErrorMessageRunFacet,
            SchemaDatasetFacet,
            SchemaField,
            StorageDatasetFacet,
        )

        run_facets = {}
        if "errorMessage" in event.run_facets:
            err = event.run_facets["errorMessage"]
            stack_trace = None
            if err.get("description"):
                stack_trace = "\n".join(
                    f"  [{a['column'] or a['assertion']}] {a['message']}"
                    for a in err["description"]
                )
            run_facets["errorMessage"] = ErrorMessageRunFacet(
                message=err.get("message", ""),
                programmingLanguage=err.get("programmingLanguage", "python"),
                stackTrace=stack_trace,
            )

        def _build_dataset_facets(raw: dict) -> dict:
            facets: dict = {}
            if "schema" in raw:
                s = raw["schema"]
                facets["schema"] = SchemaDatasetFacet(
                    fields=[SchemaField(name=f["name"], type=f.get("type", "string"), description=f.get("description")) for f in s.get("fields", [])]
                )
            if "storage" in raw:
                st = raw["storage"]
                facets["storage"] = StorageDatasetFacet(
                    storageLayer=st.get("storageLayer", "s3"),
                    fileFormat=st.get("fileFormat", ""),
                )
            return facets

        def _build_input_facets(raw: dict) -> dict:
            input_facets: dict = {}
            if "dataQualityAssertions" in raw:
                assertions = [
                    Assertion(assertion=a["assertion"], success=a.get("success", False), column=a.get("column"))
                    for a in raw["dataQualityAssertions"]
                ]
                if assertions:
                    input_facets["dataQualityAssertions"] = DataQualityAssertionsDatasetFacet(assertions=assertions)
            if "dataQualityMetrics" in raw:
                m = raw["dataQualityMetrics"]
                col_metrics = {
                    col: ColumnMetric(nullCount=metrics.get("nullCount"))
                    for col, metrics in m.get("columnMetrics", {}).items()
                }
                input_facets["dataQualityMetrics"] = DataQualityMetricsInputDatasetFacet(
                    rowCount=m.get("rowCount"),
                    columnMetrics=col_metrics if col_metrics else None,
                )
            return input_facets

        def _to_ol_input(ref: DatasetRef) -> InputDataset:
            raw = ref.facets or {}
            input_facets = _build_input_facets(raw)
            return InputDataset(
                namespace=ref.namespace,
                name=ref.name,
                facets=_build_dataset_facets(raw),
                inputFacets=input_facets if input_facets else None,
            )

        def _to_ol_output(ref: DatasetRef) -> Dataset:
            return Dataset(
                namespace=ref.namespace,
                name=ref.name,
                facets=_build_dataset_facets(ref.facets) if ref.facets else {},
            )

        ol_event = RunEvent(
            eventType=getattr(RunState, event.state),
            eventTime=event.event_time,
            run=Run(runId=event.run_id, facets=run_facets),
            job=Job(namespace=event.namespace, name=event.job_name),
            inputs=[_to_ol_input(d) for d in event.inputs],
            outputs=[_to_ol_output(d) for d in event.outputs],
        )

        try:
            self._client.emit(ol_event)
        except Exception as exc:
            logger.warning("Failed to emit lineage event to Marquez: %s", exc)
