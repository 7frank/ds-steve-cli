# ADR-001: SQL Query Engine Strategy

**Date:** 2026-07-13
**Status:** Proposed

## Context

The platform needs a SQL access layer for querying and joining data products.

Data is stored as Parquet files across multiple S3-compatible buckets (MinIO). Different workspaces may use different storage endpoints and credentials. Current transformation jobs use Polars for in-process data manipulation, but as data volumes increase and cross-workspace joins become more common, a dedicated query abstraction is required.

The platform needs to support:

* SQL-based access to data products
* Cross-workspace queries
* Local development without mandatory infrastructure
* Future migration to distributed query execution
* Separation between storage, query execution, and transformation logic

Two SQL query engines were evaluated:

* **DuckDB** — embedded, in-process SQL engine with native Parquet support
* **Trino** — distributed SQL engine with connector-based architecture

## Decision

Use **DuckDB as the initial SQL query engine** and define an abstraction that allows migration to **Trino** when scale and concurrency requirements justify distributed execution.

The SQL layer will be separated from transformation execution. Transform jobs may use DataFrame APIs (for example Polars or Spark), while SQL consumers use the query engine abstraction.

Initial architecture:

```
Consumers
    |
    +----------------+
    |                |
 DataFrame API      SQL API
    |                |
 Polars/Spark      DuckDB
    |                |
    +----------------+
             |
        Parquet Data
             |
       S3-compatible Storage
```

Future architecture:

```
Consumers
    |
    +----------------+
    |                |
 Spark API          SQL API
    |                |
 Spark             Trino
    |                |
    +----------------+
             |
      Lakehouse Tables
             |
       Object Storage
```

## DuckDB Implementation

Because MinIO buckets may have independent endpoints and credentials per workspace, DuckDB will operate in **fetch-then-query mode**.

The storage abstraction retrieves files using workspace-specific credentials. Retrieved data is registered as DuckDB views before SQL execution.

This avoids relying on a single global S3 configuration inside DuckDB.

Example:

```python
engine = DuckDBEngine()

engine.register(
    "runn",
    get_storage("gold", "workforce-assignment"),
    "data/gold/runn.parquet"
)

engine.register(
    "sf",
    get_storage("gold", "skillfinder"),
    "data/gold/sf.parquet"
)

df = engine.query("""
    SELECT
        r.Person,
        s.jobTitle
    FROM runn r
    JOIN sf s
        ON r.Person = s.name
""")
```

## Query Engine Interface

A common interface will isolate query consumers from the underlying engine.

```python
class QueryEngine(Protocol):
    def register(
        self,
        name: str,
        storage: Storage,
        path: str
    ) -> None: ...

    def query(self, sql: str) -> Any:
        ...
```

The transform or analysis code depends only on this interface.

## Trino Migration Path

When distributed SQL execution is required, DuckDB can be replaced by Trino.

The consumer interface remains unchanged:

```python
df = engine.query("""
    SELECT r.Person, s.jobTitle
    FROM runn r
    JOIN sf s
        ON r.Person = s.name
""")
```

Only the engine implementation changes.

Future Trino architecture:

```
SQL Client
    |
Trino Coordinator
    |
Trino Workers
    |
Catalog
    |
Iceberg / Hive Tables
    |
Object Storage
```

With Trino:

* Logical names map to catalog tables
* File registration is replaced by catalog management
* Query execution becomes distributed
* Multiple users and BI tools can query concurrently

Example:

```python
engine = TrinoEngine(
    host="trino.internal",
    catalog="hive",
    schema="gold"
)

engine.register(
    "runn",
    catalog_table="workforce_assignment.gold.runn"
)

engine.register(
    "sf",
    catalog_table="skillfinder.gold.sf"
)
```

## Consequences

### DuckDB (Current)

Advantages:

* No external infrastructure required
* Runs inside existing jobs
* Simple local development model
* Works well with Parquet
* Supports cross-workspace queries through the storage abstraction

Limitations:

* Memory-bound
* Limited concurrency
* Not designed for very large distributed workloads

Installation:

```bash
pip install steve-cli[duckdb]
```

### Trino (Future)

Advantages:

* Distributed query execution
* Handles larger datasets
* Supports many concurrent users
* Integrates with catalogs and lakehouse architectures

Costs:

* Requires operational infrastructure
* Requires catalog management
* Requires table registration instead of direct file paths

## Alternatives Considered

### Trino immediately

Rejected because:

* Adds infrastructure complexity before required
* Requires catalog and cluster operations
* Slows local development workflows

### DuckDB without abstraction

Rejected because:

* Creates migration lock-in
* Makes future distributed execution harder

### Spark SQL as the SQL layer

Not selected as the primary SQL interface because Spark is optimized for batch transformation workloads. Spark SQL remains an option for Spark-based transformation jobs.

## Final Decision

Adopt DuckDB as the embedded SQL engine behind a stable `QueryEngine` interface.

Design data products and SQL consumers so that Trino can replace DuckDB when distributed query execution becomes necessary.

The SQL queries written by consumers should remain unchanged during migration.
