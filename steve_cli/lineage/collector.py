from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

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

    def record_read(self, dataset: str) -> None:
        self._inputs.add(dataset)

    def record_write(self, dataset: str) -> None:
        self._outputs.add(dataset)

    def _build_event(self, state: str, run_facets: dict | None = None) -> LineageEvent:
        return LineageEvent(
            job_name=self.job_name,
            namespace=self.namespace,
            run_id=self.run_id,
            state=state,
            inputs=[DatasetRef(namespace=self.namespace, name=x) for x in self._inputs],
            outputs=[DatasetRef(namespace=self.namespace, name=x) for x in self._outputs],
            run_facets=run_facets or {},
        )

    def start(self) -> None:
        self.port.emit(self._build_event("START"))

    def complete(self) -> None:
        self.port.emit(self._build_event("COMPLETE"))

    def fail(self, error: Exception) -> None:
        self.port.emit(
            self._build_event(
                "FAIL",
                run_facets={
                    "errorMessage": {
                        "message": str(error),
                        "programmingLanguage": "python",
                    }
                },
            )
        )


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
        url = os.getenv("OPENLINEAGE_URL", "http://localhost:5000/api/v1/lineage")
        prov = provider or os.getenv("LINEAGE_PROVIDER", "openlineage")
        kwargs = {"url": url} if prov == "openlineage" else {}
        try:
            port = LineageRegistry.create(prov, **kwargs)
        except Exception:
            from .adapters.null import NullLineageAdapter

            port = NullLineageAdapter()

    return LineageSession(namespace=ns, job_name=jn, port=port)
