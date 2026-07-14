# ADR-002: Adopt Apache Iceberg as the Table Format for Data Products

**Date:** 2026-07-14
**Status:** Proposed

## Context

The platform currently stores data products as Parquet files in S3-compatible object storage (MinIO).

The current architecture provides direct access to files:

```text
Object Storage
      |
      v
 Parquet Files
      |
      +--> DuckDB SQL
      +--> Polars transformations
      +--> Future Spark jobs
```

This approach is sufficient for initial workloads but creates limitations as the platform evolves toward a multi-engine data architecture.

Future requirements include:

* Multiple compute engines accessing the same data products
* Domain-owned data products with stable contracts
* SQL access through query engines such as DuckDB and Trino
* Distributed processing through Spark
* Schema evolution
* Safe concurrent writes
* Table-level governance independent of physical storage layout

Parquet provides an efficient file format but does not provide table semantics. It does not manage:

* Table metadata
* Schema versions
* Snapshots
* Transactional updates
* File lifecycle
* Consistent reads during concurrent writes

Apache Iceberg provides a table abstraction on top of Parquet files while remaining independent of compute engines.

## Decision

Adopt Apache Iceberg as the table format for governed data products.

Data products will be represented as Iceberg tables rather than direct collections of Parquet files.

The target architecture:

```text
                 Query / Compute Layer

        +------------+------------+
        |            |            |
      DuckDB       Trino       Spark
      local        SQL         ETL
        |            |            |
        +------------+------------+
                     |
              Iceberg Tables
                     |
              Parquet Data Files
                     |
             Object Storage
             (MinIO / S3)
```

The physical storage format remains Parquet. Iceberg provides the metadata and table management layer.

Consumers access logical tables rather than storage paths.

Example:

Before:

```text
s3://workspace-a/gold/customer/*.parquet
```

After:

```text
catalog.gold.customer
```

## Iceberg Responsibilities

Iceberg becomes responsible for:

### Table Metadata

Managing the relationship between logical tables and physical files.

Example:

```text
gold.customer table

Snapshot 42
 |
 +-- data/customer-001.parquet
 +-- data/customer-002.parquet
```

### Schema Evolution

Schema changes are tracked as table metadata changes.

Example:

```text
Schema v1

customer_id
name


Schema v2

customer_id
name
country
```

Consumers can continue using compatible versions.

### Snapshot-Based Reads

Readers see consistent table versions.

Example:

```text
Snapshot 100
    old files

Snapshot 101
    new files
```

A query executes against a complete snapshot instead of partially updated files.

### File Management

Iceberg metadata enables:

* file pruning
* partition evolution
* compaction strategies
* removal of obsolete files

## Implementation Approach

The initial implementation will continue supporting local development through DuckDB.

Example:

```python
engine = DuckDBEngine()

df = engine.query("""
    SELECT *
    FROM gold.customer
""")
```

The future distributed implementation uses Trino:

```python
engine = TrinoEngine(
    catalog="iceberg",
    schema="gold"
)

df = engine.query("""
    SELECT *
    FROM customer
""")
```

The SQL contract remains unchanged.

## Consequences

### Positive

* Data products become engine-independent
* Spark, Trino, DuckDB, and other engines can share the same tables
* Storage layout is hidden from consumers
* Schema changes become manageable
* Snapshot-based consistency is available
* Enables future Data Mesh patterns

### Negative

* Additional metadata infrastructure is required
* Requires a catalog implementation
* Table lifecycle management becomes a platform responsibility
* More operational complexity compared to raw Parquet files

## Alternatives Considered

### Continue using raw Parquet files

Rejected as the long-term data product contract.

Advantages:

* Simple
* No additional components

Disadvantages:

* Consumers depend on file paths
* Schema management is manual
* Multiple engines interpret datasets independently
* Concurrent writes are difficult to manage

### Delta Lake

Not selected initially because the target architecture prioritizes interoperability across a broad range of query engines and lakehouse tools.

### Database warehouse tables

Not selected because the platform requires object storage based data products and separation between storage and compute.

## Migration Path

### Phase 1: Current

```text
MinIO
 |
Parquet
 |
DuckDB / Polars
```

### Phase 2: Introduce Iceberg

```text
MinIO
 |
Iceberg metadata
 |
Parquet files
 |
DuckDB
```

### Phase 3: Add distributed SQL

```text
MinIO
 |
Iceberg catalog
 |
Trino
 |
SQL consumers
```

### Phase 4: Add distributed transformations

```text
MinIO
 |
Iceberg catalog
 |
Spark
 |
Domain data products
```

## Final Decision

Adopt Apache Iceberg as the long-term table abstraction for data products.

Raw Parquet files remain the storage format, but Iceberg becomes the contract between storage and compute engines.

This allows the platform to start with lightweight local execution and evolve toward distributed query and processing without redesigning data products.
