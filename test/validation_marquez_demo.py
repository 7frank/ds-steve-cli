#!/usr/bin/env python3
import logging

import polars as pl

from steve_cli.decorators import lineage_job
from steve_cli.validation.adapters.validoopsie import ValidoopsieAdapter

logger = logging.getLogger(__name__)

SOURCE_PATH = "demo/orders.csv"
TARGET_PATH = "demo/orders_clean.csv"


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

    raw = bronze.get_bytes(SOURCE_PATH)
    logger.info("Read %d bytes from bronze/%s", len(raw), SOURCE_PATH)

    df = pl.read_csv(raw)

    dq_validator = build_validator()
    result = dq_validator.validate(df)

    print(f"\nValidation ({result.framework}): {'✓ passed' if result.success else '✗ failed'}")
    print(f"  checks: {result.checks_passed}/{result.checks_total} passed")
    if result.failures:
        for f in result.failures:
            print(f"  ✗ {f.check_name} [{f.column}]: {f.message}")

    bronze.attach_validation(SOURCE_PATH, result)

    df_clean = df.filter(
        pl.col("customer_id").is_not_null()
        & (pl.col("amount") > 0)
        & (pl.col("country").str.len_chars() == 2)
    ).with_columns(
        (pl.col("amount") * 1.19).round(2).alias("amount_incl_vat")
    )
    print(f"\nDropped {len(df) - len(df_clean)} invalid rows, kept {len(df_clean)}")

    cleaned = df_clean.write_csv().encode()

    try:
        silver.put_bytes(cleaned, TARGET_PATH)
        logger.info("Wrote %d bytes to silver/%s", len(cleaned), TARGET_PATH)
    except EnvironmentError:
        logger.warning("S3 not available, skipping write")


if __name__ == "__main__":
    import os
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    run()
