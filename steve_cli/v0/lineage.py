from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, List
import uuid

from openlineage.client import OpenLineageClient
from openlineage.client.event_v2 import (
    RunEvent,
    RunState,
    Job,
    Run,
    Dataset,
)
from openlineage.client.facet import (
    SchemaDatasetFacet,
)


class LineageCollector(Protocol):
    def record_read(self, dataset: str) -> None:
        ...

    def record_write(self, dataset: str) -> None:
        ...

    def complete(self) -> None:
        ...


@dataclass
class LineageContext:
    namespace: str
    job_name: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    inputs: set[str] = field(default_factory=set)
    outputs: set[str] = field(default_factory=set)


class OpenLineageCollector(LineageCollector):

    def __init__(
        self,
        namespace: str,
        job_name: str,
        url: str,
    ):
        self.context = LineageContext(
            namespace=namespace,
            job_name=job_name,
        )

        self.client = OpenLineageClient(url)

    def record_read(self, dataset: str):
        self.context.inputs.add(dataset)

    def record_write(self, dataset: str):
        self.context.outputs.add(dataset)

    def complete(self):

        event = RunEvent(
            eventType=RunState.COMPLETE,
            eventTime=datetime.now(timezone.utc).isoformat(),
            run=Run(
                runId=self.context.run_id,
            ),
            job=Job(
                namespace=self.context.namespace,
                name=self.context.job_name,
            ),
            inputs=[
                Dataset(
                    namespace="s3",
                    name=x,
                )
                for x in self.context.inputs
            ],
            outputs=[
                Dataset(
                    namespace="s3",
                    name=x,
                )
                for x in self.context.outputs
            ],
        )

        self.client.emit(event)



class NullLineageCollector:

    def record_read(self, dataset: str) -> None:
        pass

    def record_write(self, dataset: str) -> None:
        pass

    def complete(self) -> None:
        pass        