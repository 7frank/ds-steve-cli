from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict

from .port import DatasetRef, LineageEvent, LineagePort
from .registry import LineageRegistry


@dataclass
class LineageSession:
    namespace: str
    job_name: str
    port: LineagePort
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _inputs: set[str] = field(default_factory=set)
    _outputs: set[str] = field(default_factory=set)
    _facets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _dataset_namespaces: Dict[str, str] = field(default_factory=dict)

    def record_read(self, dataset: str, facets: Dict[str, Any] | None = None, namespace: str | None = None) -> None:
        self._inputs.add(dataset)
        if facets:
            self._facets.setdefault(dataset, {}).update(facets)
        if namespace:
            self._dataset_namespaces[dataset] = namespace

    def record_write(self, dataset: str, facets: Dict[str, Any] | None = None, namespace: str | None = None) -> None:
        self._outputs.add(dataset)
        if facets:
            self._facets.setdefault(dataset, {}).update(facets)
        if namespace:
            self._dataset_namespaces[dataset] = namespace

    def attach_facets(self, dataset: str, facets: Dict[str, Any]) -> None:
        self._facets.setdefault(dataset, {}).update(facets)

    def attach_validation(self, dataset: str, result: Any) -> None:
        facet = result.to_openlineage_facet()
        self._facets.setdefault(dataset, {}).update(facet)

    def _build_event(self, state: str, run_facets: dict | None = None) -> LineageEvent:
        return LineageEvent(
            job_name=self.job_name,
            namespace=self.namespace,
            run_id=self.run_id,
            state=state,
            inputs=[DatasetRef(namespace=self._dataset_namespaces.get(x, self.namespace), name=x, facets=self._facets.get(x, {})) for x in self._inputs],
            outputs=[DatasetRef(namespace=self._dataset_namespaces.get(x, self.namespace), name=x, facets=self._facets.get(x, {})) for x in self._outputs],
            run_facets=run_facets or {},
        )

    def start(self) -> None:
        self.port.emit(self._build_event("START"))

    def complete(self) -> None:
        self.port.emit(self._build_event("COMPLETE"))

    def fail(self, error: Exception) -> None:
        from steve_cli.validation.port import DataQualityError
        error_facet: Dict[str, Any] = {
            "message": str(error),
            "programmingLanguage": "python",
        }
        if isinstance(error, DataQualityError):
            error_facet["description"] = [
                {"assertion": f.check_name, "column": f.column, "message": f.message}
                for f in error.failures
            ]
        self.port.emit(self._build_event("FAIL", run_facets={"errorMessage": error_facet}))


def make_session(
    namespace: str | None = None,
    job_name: str | None = None,
    provider: str | None = None,
    enabled: bool = True,
) -> LineageSession:
    ns = namespace or os.getenv("OPENLINEAGE_NAMESPACE", "default")
    jn = job_name or os.getenv("OPENLINEAGE_JOB_NAME", "unknown_job")

    if not enabled:
        from .adapters.null import NullLineageAdapter

        port: LineagePort = NullLineageAdapter()
    else:
        url = os.getenv("OPENLINEAGE_URL")
        prov = provider or os.getenv("LINEAGE_PROVIDER", "openlineage")
        if prov == "openlineage" and not url:
            import logging
            logging.getLogger(__name__).warning(
                "Lineage disabled: OPENLINEAGE_URL is not set"
            )
            from .adapters.null import NullLineageAdapter

            port = NullLineageAdapter()
        else:
            kwargs = {"url": url} if prov == "openlineage" else {}
            try:
                port = LineageRegistry.create(prov, **kwargs)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Lineage disabled: could not initialize provider %r: %s", prov, exc
                )
                from .adapters.null import NullLineageAdapter
                port = NullLineageAdapter()

    return LineageSession(namespace=ns, job_name=jn, port=port)
