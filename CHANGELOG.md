# Changelog

## [1.0.0] — 2026-09-18

### Breaking Changes — Data Migration Required

This release introduces **branch-isolated storage**. All storage paths and Trino schema names have changed. **Existing data must be migrated manually.**

#### S3 paths

All files are now stored under a `_store/{branch}/` root prefix.

| Before | After |
|---|---|
| `s3://bucket/orders/2024.parquet` | `s3://bucket/_store/main/orders/2024.parquet` |
| `s3://bucket/raw/data.csv` | `s3://bucket/_store/main/raw/data.csv` |

**Migration:**
```bash
# Copy all existing objects into the new _store/main/ prefix
aws s3 cp s3://your-bucket/ s3://your-bucket/_store/main/ \
  --recursive \
  --exclude "_store/*" \
  --exclude "_iceberg/*"
```

#### Iceberg / Trino — schema names

Schemas now follow the pattern `{workspace}_{tier}__{branch}` (no `ws_` prefix, double underscore before branch).

| Before | After |
|---|---|
| `ws_myworkspace_bronze` | `myworkspace_bronze__main` |
| `ws_myworkspace_silver` | `myworkspace_silver__main` |

**Migration:**
1. Create the new schema in Trino: `CREATE SCHEMA minio.myworkspace_bronze__main`
2. Re-register or move your Iceberg tables into the new schema, or re-run your ETL pipelines targeting the new schema.
3. Drop the old schema once data is verified: `DROP SCHEMA minio.ws_myworkspace_bronze`

#### Iceberg underlying S3 location

Iceberg data and metadata files are now written under `_iceberg/{branch}/` in the bucket instead of the Lakekeeper warehouse root.

New namespaces created by `TrinoStorage` automatically set their `location` property to `_iceberg/{branch}/`.

---

### New Features

- **`steve_cli.storage.get_branch_prefix()`** — resolves the active git branch from (in priority order): `STEVE_BRANCH` env var, `GITHUB_HEAD_REF`, `GITHUB_REF_NAME`, `git rev-parse --abbrev-ref HEAD`, or falls back to `"main"`.
- **`_meta` table** — each new Iceberg namespace gets a `_meta` table with `branch`, `tier`, `schema_name`, `iceberg_location`, `created_at` for self-documentation in schema browsers.
- **Branch isolation** — developers and CI pipelines automatically write to branch-scoped paths, preventing data pollution between branches.

---

### Controlling the branch

| Scenario | How |
|---|---|
| Production ETL on `main` | No action needed — git HEAD or `GITHUB_REF_NAME` resolves to `main` |
| Force a specific branch | `STEVE_BRANCH=feat_my_fn` |
| GitHub Actions PR | `GITHUB_HEAD_REF` is set automatically |
| No git available | Defaults to `main` |

---

## [0.5.6] and earlier

See git history.
