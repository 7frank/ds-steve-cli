# Steve CLI

A CLI tool for the data platform. Commands are organized by concern: general workspace automation, data lakehouse operations, semantic knowledge graphs, and governance.

## Installation

```bash
# Add to your workspace
uv add steve-cli

# Or pin to a local editable copy (in your workspace pyproject.toml)
[tool.uv.sources]
steve-cli = { path = "../../packages/steve-cli", editable = true }
```

Upgrade to the latest version:

```bash
steve upgrade
```

---

## Platform

General-purpose commands for running jobs, managing local development processes, and working with the core infrastructure (object storage, secrets).

### `steve jobs` — run automation jobs

Runs tasks defined in `jobs.yaml`. Each job declares a command and optional env vars. Designed for data pipeline steps, seeding, bootstrapping, etc.

```bash
steve jobs              # interactive picker
steve jobs ls           # list all jobs with commands and env vars
steve jobs run <name>   # run a job by name
steve jobs run <name> -f path/to/jobs.yaml
```

`jobs.yaml` example:

```yaml
jobs:
  - name: seed-data
    command: ["uv", "run", "src/02_seed_data.py"]
    env:
      BATCH_SIZE: "500"
```

### `steve apps` — manage long-running apps

Starts, stops, and monitors local apps defined in `apps.yaml`. Apps are registered with the auth-proxy so they get a public URL inside the platform.

```bash
steve apps              # interactive picker
steve apps ls           # list apps with status and URLs
steve apps status       # show running/stopped status for all apps
steve apps start <name>
steve apps stop <name>
steve apps restart <name>
steve apps logs <name> [-n 100]
```

### `steve setup env` — decrypt secrets

Decrypts SOPS-encrypted `*.enc.env` files in the current directory and writes plaintext `.env` files.

```bash
steve setup env
```

### `steve buckets` — browse S3 storage

Interactive browser for S3/MinIO buckets. Detects available buckets from env vars (`BRONZE_ACCESS_KEY`, `SILVER_ACCESS_KEY`, `GOLD_ACCESS_KEY`, or `{WORKSPACE}_ACCESS_KEY`) and shows their contents as a file tree. Files can be opened in visidata.

```bash
steve buckets
```

---

## Data Lakehouse

Commands for working with the lakehouse layer: Iceberg tables accessed via Trino.

### `steve tables` — browse Iceberg tables via Trino

Interactive browser for Iceberg tables. Lists schemas and tables available via Trino, and opens them in visidata for inspection.

```bash
steve tables
```

Requires `TRINO_ENDPOINT` to be set. Install visidata for table preview:

```bash
uv pip install 'steve-cli[visidata]'
```

---

## Semantics & Knowledge Graphs

Commands for authoring and operating Virtual Knowledge Graphs (VKGs) — semantic layers that expose relational Iceberg data as RDF/SPARQL via Ontop.

There are two sides:
- **`ontology`** — the *author/publish* side: define concepts, register them in the Data Product Registry, compile VKG artifacts
- **`vkg`** — the *runtime/ops* side: manage named Ontop instances on the VKG control plane, run SPARQL queries

### `steve ontology` — author and publish ontologies

```bash
steve ontology ls                          # list registered packages in the registry
steve ontology ls --show-bindings          # include binding packages
steve ontology register <file.yaml>        # register a concept or binding YAML
steve ontology add <uri>                   # add a dependency URI to ontology.yaml
steve ontology build                       # resolve deps + compile → write vkg/ folder locally
steve ontology compile --root <uri> --binding <uri>   # compile and print artifact info
steve ontology push                        # compile + upload to VKG control plane (default slot)
steve ontology push --name <slot>          # push to a named VKG slot
steve ontology push -f ontology-consumer.yaml  # use a different manifest file
steve ontology push --frozen               # fail if lock file is absent or stale (like npm ci)
```

Manifest format (`ontology.yaml`):

```yaml
# Option A: workspace that owns ontology files
packages:
  - path: ontologies/order_core.yaml
bindings:
  - path: ontologies/binding_order_iceberg.yaml
compile:
  root: dp://o/acme/concepts/order/core
  binding: dp://o/acme/bindings/order/iceberg

# Option B: consumer workspace (resolves deps from registry, no local files)
dependencies:
  - dp://o/acme/concepts/party/core
  - dp://o/acme/concepts/order/core
compile:
  root: dp://o/acme/concepts/order/core
  binding: dp://o/acme/bindings/order/iceberg
```

After `push`, a lock file (`ontology.lock.yaml`) is written with resolved package versions and Iceberg snapshot IDs for audit and future time-travel SQL rewriting.

### `steve vkg` — operate VKG instances

```bash
steve vkg ls                              # list all slots: status, concept counts, SPARQL URL
steve vkg status <name>                   # detailed status for one slot
steve vkg start <name>                    # start a stopped slot
steve vkg stop <name>                     # stop a running slot (persists desired=stopped)
steve vkg sparql <name> "<SPARQL query>"  # run a SPARQL query and print results as a table
steve vkg sparql <name> "<query>" --format json
steve vkg logs <name> [-n 50]            # tail the Ontop process log for a slot
```

Desired state is persisted per slot — slots with `desired=running` are automatically restored after a pod restart.

---

## Governance

### `steve policies apply` — apply access policies

Applies role-based access policies from a YAML file to the Policy Control Plane.

```bash
steve policies apply                      # apply from ./policies/access.yaml
steve policies apply -f custom/path.yaml
```

---

## Environment variables

| Variable | Used by | Description |
|---|---|---|
| `REGISTRY_URL` | `ontology` | Data Product Registry URL (default: `http://localhost:8765`) |
| `REGISTRY_TOKEN` | `ontology` | Pre-issued JWT for the registry |
| `REGISTRY_JWT_SECRET` | `ontology` | Secret to mint a JWT (if no token) |
| `REGISTRY_ORG` | `ontology` | Org claim for minted JWT |
| `VKG_CONTROL_PLANE_URL` | `ontology push`, `vkg` | Control plane URL (default: `http://localhost:18081`) |
| `TRINO_ENDPOINT` | `ontology push`, `tables` | Trino HTTP URL for snapshot capture and table browsing |

---

## File Metadata Extraction

Steve automatically extracts metadata from files read or written via `MetadataRegistry`. The extractor is chosen by file extension.

| Extension | Extractor | Requires |
|---|---|---|
| `.parquet`, `.pq` | `ParquetExtractor` | `pip install steve-cli[polars]` |
| `.csv`, `.tsv`, `.txt` | `CsvExtractor` | stdlib only |
| `.json`, `.jsonl`, `.ndjson` | `JsonExtractor` | stdlib only |
| `.xlsx`, `.xls`, `.xlsm` | `ExcelExtractor` | `pip install steve-cli[excel]` |
| anything else | `GenericExtractor` | stdlib only |

Custom extractors can be registered at startup:

```python
from steve_cli.storage.metadata.port import MetadataExtractorPort, FileMetadata, ColumnMetadata
from steve_cli.storage.metadata.registry import MetadataRegistry

class AvroExtractor(MetadataExtractorPort):
    extensions = (".avro",)

    def extract(self, data: bytes, path: str) -> FileMetadata:
        import fastavro, io
        reader = fastavro.reader(io.BytesIO(data))
        schema = reader.writer_schema
        columns = [ColumnMetadata(name=f["name"], type=str(f["type"])) for f in schema.get("fields", [])]
        return FileMetadata(format="avro", size_bytes=len(data), rows=len(list(reader)), columns=columns)

MetadataRegistry.register("avro", AvroExtractor)
```
