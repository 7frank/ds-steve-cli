#!/usr/bin/env python3
import polars as pl

from steve_cli.decorators import lineage_job
from steve_cli.storage.metadata import MetadataRegistry
from steve_cli.validation.adapters.validoopsie import ValidoopsieAdapter

SOURCE_PATH = "demo/orders.csv"
TARGET_PATH = "demo/orders_clean.csv"

SAMPLE_CSV = b"""order_id,customer_id,amount,country,status
1,101,49.99,DE,completed
2,102,0.00,US,completed
3,103,129.50,FR,completed
4,104,-5.00,DE,completed
5,,89.00,US,pending
6,106,200.00,XX,completed
7,107,59.99,DE,completed
"""


def build_validator() -> ValidoopsieAdapter:
    suite = [
        lambda vd: vd.NullValidation.ColumnNotBeNull("customer_id"),
        lambda vd: vd.ValuesValidation.ColumnValuesToBeBetween("amount", min_value=0.01),
        lambda vd: vd.StringValidation.LengthToBeEqualTo("country", value=2),
    ]
    return ValidoopsieAdapter(suite=suite)


@lineage_job(
    name="orders-bronze-to-silver",
    namespace="demo",
    validation="null",
    lineage_provider="openlineage",
)
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

    df = pl.read_csv(raw)

    dq_validator = build_validator()
    result = dq_validator.validate(df)

    print(f"\nValidation ({result.framework}): {'✓ passed' if result.success else '✗ failed'}")
    print(f"  checks: {result.checks_passed}/{result.checks_total} passed")
    if result.failures:
        for f in result.failures:
            print(f"  ✗ {f.check_name} [{f.column}]: {f.message}")

    dq_facet = result.to_openlineage_facet()
    print(f"\nQuality facet: {dq_facet['dataQualityFacet']['checks']}")

    df_clean = df.filter(
        pl.col("customer_id").is_not_null()
        & (pl.col("amount") > 0)
        & (pl.col("country").str.len_chars() == 2)
    )
    print(f"\nDropped {len(df) - len(df_clean)} invalid rows, kept {len(df_clean)}")

    cleaned = df_clean.write_csv().encode()

    try:
        silver.put_bytes(cleaned, TARGET_PATH)
        print(f"Wrote {len(cleaned)} bytes to silver/{TARGET_PATH}")
    except (SystemExit, EnvironmentError):
        print("[demo] S3 not available, skipping write")


if __name__ == "__main__":
    run()
