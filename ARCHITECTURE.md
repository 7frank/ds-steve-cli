# Architecture: Data Lineage, Metadata & Validation

## Overview

`steve-cli` provides a lightweight SDK for S3-based ETL pipelines that automatically captures lineage, schema metadata, parquet statistics, and data quality results — and publishes them to a backend (default: Marquez via OpenLineage). All three concerns use a **port-and-adapter** pattern so the backend can be swapped without touching pipeline code.

---

## How lineage is captured

### The interception chain

```
@lineage_job decorator
        │
        ├── creates LineageSession (holds run_id, inputs, outputs)
        ├── emits START event via LineagePort
        │
        ├── wraps S3Storage in LineageStorage
        │       │
        │       │  every get_bytes / get_file call
        │       │    → session.record_read(path)
        │       │
        │       │  every put_bytes / put_file call
        │       │    → session.record_write(path)
        │
        ├── calls your function with (storage=, validator=)
        │
        ├── on success → emits COMPLETE event
        └── on exception → emits FAIL event with error message facet
```

`LineageStorage` is a transparent decorator: it delegates every call to the real `S3Storage` and then notifies the session. Pipeline code never knows it is being observed.

### What a RunEvent looks like

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2025-01-15T12:34:56Z",
  "run": {
    "runId": "6b3c8e4f-1234-5678-abcd-ef0123456789"
  },
  "job": {
    "namespace": "analytics",
    "name": "customer_pipeline"
  },
  "inputs": [
    { "namespace": "analytics", "name": "raw/customers.parquet" }
  ],
  "outputs": [
    { "namespace": "analytics", "name": "gold/customers.parquet" }
  ]
}
```

This event is sent to Marquez at `POST /api/v1/lineage`. Marquez stores the job graph and makes it queryable.

---

## How validation results are stored

Validation results are converted to an **OpenLineage custom facet** and attached to the RunEvent's dataset facets.

### Flow

```
validator.validate(df)
        │
        └── returns ValidationResult
                │
                └── .to_openlineage_facet()
                        │
                        └── dict attached to DatasetRef.facets
                                │
                                └── sent inside the RunEvent to Marquez
```

### What the facet looks like

```json
{
  "dataQualityFacet": {
    "_producer": "steve-cli",
    "framework": "validoopsie",
    "success": true,
    "checks": {
      "total": 25,
      "passed": 24,
      "failed": 1
    },
    "failures": [
      {
        "check": "customer_id_not_null",
        "message": "Found 3 null values",
        "column": "customer_id"
      }
    ]
  }
}
```

Marquez displays this under the dataset's facets tab for each run.

---

## How parquet/schema metadata is stored

When you call `extract_parquet_metadata(data)` on a file's bytes, PyArrow reads the parquet footer and returns a `ParquetMetadata` object. Calling `.to_openlineage_facet()` on it produces three standard OpenLineage facets:

```json
{
  "datasetFacets": {
    "schema": {
      "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/SchemaDatasetFacet.json",
      "fields": [
        { "name": "customer_id", "type": "string" },
        { "name": "created_at", "type": "timestamp[us]" }
      ]
    },
    "storage": {
      "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/StorageDatasetFacet.json",
      "storageLayer": "s3",
      "fileFormat": "parquet"
    },
    "parquet": {
      "rows": 500000,
      "rowGroups": 4,
      "parquetVersion": "2.6",
      "compression": "snappy"
    }
  }
}
```

Attach these to a `DatasetRef.facets` before passing to `LineageSession`.

---

## Port-and-adapter pattern

Both lineage emission and validation use the same structural pattern:

```
Pipeline code
     │
     │  depends only on
     ▼
  Port (ABC / Protocol)
     │
     │  implemented by
     ├──► Adapter A  (default)
     ├──► Adapter B
     └──► Null Adapter (no-op, for tests / disabled)
```

The pipeline never imports a concrete adapter. The registry resolves the adapter at runtime from an env var.

---

## Lineage port and adapters

### Port

```python
# steve_cli/lineage/port.py

class LineagePort(ABC):
    def emit(self, event: LineageEvent) -> None: ...
```

`LineageEvent` is a plain dataclass carrying state, job, run_id, inputs, outputs, and run_facets. It is backend-agnostic.

### Available adapters

| Name            | Class                   | Requires              | Description                          |
|-----------------|-------------------------|-----------------------|--------------------------------------|
| `openlineage`   | `OpenLineageAdapter`    | `openlineage-python`  | Default. Posts to Marquez/any OL API |
| `logging`       | `LoggingLineageAdapter` | nothing               | Writes structured log lines          |
| `null`          | `NullLineageAdapter`    | nothing               | Discards all events (for tests)      |

### Switching provider

```bash
LINEAGE_PROVIDER=logging   # structured logs, no Marquez needed
LINEAGE_PROVIDER=null      # completely silent
LINEAGE_PROVIDER=openlineage  # default
OPENLINEAGE_URL=http://marquez:5000/api/v1/lineage
OPENLINEAGE_NAMESPACE=analytics
```

### Registering a custom adapter

```python
from steve_cli.lineage.port import LineagePort, LineageEvent
from steve_cli.lineage.registry import LineageRegistry

class DataHubAdapter(LineagePort):
    def emit(self, event: LineageEvent) -> None:
        # post to DataHub REST emitter
        ...

LineageRegistry.register("datahub", DataHubAdapter)
```

Then set `LINEAGE_PROVIDER=datahub`.

---

## Validation port and adapters

### Port

```python
# steve_cli/validation/port.py

class ValidationPort(ABC):
    def validate(self, dataframe, context=None) -> ValidationResult: ...
```

`ValidationResult` is a plain dataclass. Pipeline code never imports `validoopsie` or `great_expectations` directly.

### Available adapters

| Name                  | Class                        | Requires               | Engine  |
|-----------------------|------------------------------|------------------------|---------|
| `null`                | `NullValidationAdapter`      | nothing                | —       |
| `validoopsie`         | `ValidoopsieAdapter`         | `validoopsie`, `polars`| Polars  |
| `great_expectations`  | `GreatExpectationsAdapter`   | `great-expectations`, `pandas` | Pandas |

### Switching provider

```bash
VALIDATION_PROVIDER=validoopsie
VALIDATION_PROVIDER=great_expectations
VALIDATION_PROVIDER=null   # default, no-op
```

---

## What Marquez shows end-to-end

After a pipeline run, Marquez will display:

**Lineage graph**
```
s3://bronze/raw/customers.parquet
              │
              ▼
     customer_pipeline  (run: 6b3c8e4f, COMPLETE)
              │
              ▼
s3://gold/gold/customers.parquet
```

**Per-run facets**
- Run state: COMPLETE / FAIL
- Error message (on failure)

**Per-dataset facets**
- Schema: column names and types
- Storage: S3, parquet
- Parquet statistics: rows, row groups, compression
- Data quality: framework, pass/fail counts, failure details

---

## Environment variables reference

| Variable              | Default                                  | Purpose                              |
|-----------------------|------------------------------------------|--------------------------------------|
| `LINEAGE_PROVIDER`    | `openlineage`                            | Which lineage adapter to use         |
| `OPENLINEAGE_URL`     | `http://localhost:5000/api/v1/lineage`   | Marquez (or other OL API) endpoint   |
| `OPENLINEAGE_NAMESPACE` | `default`                              | Namespace for jobs and datasets      |
| `OPENLINEAGE_JOB_NAME` | `unknown_job`                           | Fallback job name                    |
| `VALIDATION_PROVIDER` | `null`                                   | Which validation adapter to use      |

---

## Example pipeline

```python
from steve_cli.decorators import lineage_job
from steve_cli.storage.parquet import extract_parquet_metadata

@lineage_job(
    name="daily_customer_load",
    namespace="analytics",
    validation="validoopsie",
)
def execute(storage, validator):
    raw = storage.get_bytes("raw/customers.parquet")

    meta = extract_parquet_metadata(raw)

    import polars as pl
    df = pl.read_parquet(raw)

    result = validator.validate(df)

    df_clean = df.filter(pl.col("customer_id").is_not_null())

    storage.put_bytes(df_clean.write_parquet(), "gold/customers.parquet")

    return result
```

The decorator handles START/COMPLETE/FAIL events, input/output tracking, and wiring of the validator. The pipeline function only calls business logic.
