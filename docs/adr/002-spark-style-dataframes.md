# ADR-001: Adopt Spark DataFrame API as the Standard ETL Transformation Interface

## Status

Draft

## Context

The data platform requires a consistent transformation interface for building ETL and data product pipelines.

Current ETL workloads are implemented as custom jobs packaged with Docker images and executed through job definitions. As data volumes and processing requirements increase, some workloads may require distributed execution.

The platform needs an interface that allows:

* Local development without requiring a cluster
* Migration to distributed execution when required
* Consistent transformation patterns across domains
* Reuse of existing engineering practices

Spark provides a DataFrame API that supports local execution and distributed execution using the same programming model.

Example:

```python
df = spark.read.parquet("s3://lake/events")

result = (
    df
    .filter(df.country == "DE")
    .groupBy("customer_id")
    .sum("amount")
)
```

The same transformation code can run locally:

```python
.master("local[*]")
```

or on a production Spark cluster.

## Decision

Adopt the Spark DataFrame API as the default transformation interface for scalable ETL workloads.

Spark applications will:

* Read and write open table formats such as Parquet, Iceberg, or Delta
* Implement transformations using Spark DataFrame operations
* Be packaged as containerized jobs
* Support local execution for development and testing
* Support cluster execution without changing business transformation logic

The platform will not require every workload to run on a Spark cluster. Execution mode will depend on workload size and performance requirements.

## Consequences

### Positive

* ETL code can scale from local development to distributed execution
* Teams use a consistent transformation model
* Large joins and aggregations can execute across multiple workers
* Spark ecosystem integrations are available

### Negative

* Spark introduces runtime overhead for small workloads
* Developers must follow distributed processing patterns
* Arbitrary Python operations can reduce performance
* Local behavior may differ from large cluster execution

## Alternatives Considered

### Continue using pandas only

Rejected because:

* Large datasets require manual scaling strategies
* Memory limitations become a bottleneck
* Distributed execution requires significant rewrites

### Use Spark only in production

Rejected because:

* Development and production environments would use different programming models
* Migration costs increase when workloads grow

### Use multiple dataframe frameworks

Possible future direction, but introduces additional complexity around APIs, testing, and developer experience.

## Implementation Notes

Initial deployment:

```
Docker
  |
Spark application
  |
Local execution
```

Future deployment:

```
Docker
  |
Spark application
  |
Kubernetes / Databricks / YARN cluster
```

The transformation layer should avoid dependencies on local filesystem access, single-node assumptions, or row-by-row processing.
