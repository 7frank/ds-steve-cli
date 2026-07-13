# ADR 001: SQL Query Engine Strategy

**Date:** 2026-07-13  
**Status:** Proposed

## Context

The platform stores data as Parquet files across multiple S3-compatible buckets (MinIO), potentially with different endpoints and credentials per workspace. Transform jobs currently use Polars for in-process data manipulation, but as data volumes grow and cross-workspace joins become common, a dedicated SQL query engine is needed.

Two candidates were evaluated:

- **DuckDB** — embeddable, in-process SQL engine with native Parquet/S3 support
- **Trino** — distributed SQL engine with connector-based architecture, requires external infrastructure (coordinator, Hive Metastore)

## Decision

Use **DuckDB** for now, design the interface to allow a **Trino** swap later.

Since MinIO buckets may have different endpoints and credentials per workspace, DuckDB will operate in a **fetch-then-query** mode: each storage's `get_bytes()` is called to load files into memory, which are registered as in-memory views before executing SQL. This avoids the limitation of DuckDB's single global S3 config.

A `QueryEngine` protocol will be defined so both engines implement the same interface:

```python
class QueryEngine(Protocol):
    def register(self, name: str, storage: Storage, path: str) -> None: ...
    def query(self, sql: str) -> Any: ...  # returns polars DataFrame
```

## Consequences

**DuckDB (now):**
- No infrastructure required — runs in-process
- Cross-workspace joins work regardless of differing endpoints/credentials
- Memory-bound — not suitable for very large datasets
- Installation: `pip install steve-cli[duckdb]`

**Trino (future migration):**
- Requires a running Trino coordinator + Hive Metastore
- Tables must be registered in a catalog (not ad-hoc file paths)
- `register()` maps logical names → `catalog.schema.table`
- Connection via `trino-python-client`
- Migration: swap `DuckDBEngine` for `TrinoEngine`, keep the same `query()` call in transform scripts

## Migration Path

```python
# Today
engine = DuckDBEngine()
engine.register("runn", get_storage("gold", "workforce-assignment"), "data/gold/runn.parquet")
engine.register("sf", get_storage("gold", "skillfinder"), "data/gold/skillfinder.parquet")
df = engine.query("SELECT r.Person, s.jobTitle FROM runn r JOIN sf s ON r.Person = s.name")

# Future (Trino)
engine = TrinoEngine(host="trino.internal", catalog="hive", schema="gold")
engine.register("runn", catalog_table="workforce_assignment.gold.runn")
engine.register("sf", catalog_table="skillfinder.gold.skillfinder")
df = engine.query("SELECT r.Person, s.jobTitle FROM runn r JOIN sf s ON r.Person = s.name")
```

The transform script SQL does not change.
