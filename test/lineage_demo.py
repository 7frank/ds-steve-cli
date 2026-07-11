#!/usr/bin/env python3
from steve_cli.decorators import lineage_job
from steve_cli.storage.metadata import MetadataRegistry

SOURCE_PATH = "demo/customers.csv"
TARGET_PATH = "demo/customers_processed.csv"

SAMPLE_CSV = b"""customer_id,name,email,country
1,Alice,alice@example.com,DE
2,Bob,bob@example.com,US
3,Carol,carol@example.com,FR
4,,missing@example.com,DE
5,Eve,eve@example.com,US
"""


@lineage_job(name="lineage-demo", namespace="demo", validation="null", lineage_provider="logging")
def run(get_storage, validator):
    bronze = get_storage("bronze")
    silver = get_storage("silver")

    try:
        raw = bronze.get_bytes(SOURCE_PATH)
        print(f"Read {len(raw)} bytes from bronze/{SOURCE_PATH}")
    except (SystemExit, EnvironmentError):
        print("[demo] S3 not available, using built-in sample data")
        raw = SAMPLE_CSV

    meta = MetadataRegistry.extract(raw, SOURCE_PATH)
    print(f"Metadata: format={meta.format}, rows={meta.rows}, columns={[c.name for c in meta.columns]}")

    lines = raw.decode().splitlines()
    header = lines[0]
    data_lines = [row for row in lines[1:] if row.split(",")[1].strip()]
    cleaned = (header + "\n" + "\n".join(data_lines) + "\n").encode()
    print(f"Dropped {len(lines) - 1 - len(data_lines)} rows with missing name")

    result = validator.validate(None)
    print(f"Validation: framework={result.framework}, success={result.success}")

    try:
        silver.put_bytes(cleaned, TARGET_PATH)
        print(f"Wrote {len(cleaned)} bytes to silver/{TARGET_PATH}")
    except (SystemExit, EnvironmentError):
        print("[demo] S3 not available, skipping write")

    facet = meta.to_openlineage_facet()
    print(f"OpenLineage facet keys: {list(facet.get('datasetFacets', {}).keys())}")


if __name__ == "__main__":
    run()
