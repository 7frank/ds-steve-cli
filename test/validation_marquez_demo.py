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
        ("error", lambda vd: vd.NullValidation.ColumnNotBeNull("customer_id")),
        ("warn",  lambda vd: vd.ValuesValidation.ColumnValuesToBeBetween("amount", min_value=0.01)),
        ("info",  lambda vd: vd.StringValidation.LengthToBeEqualTo("country", value=2)),
    ]
    return ValidoopsieAdapter(suite=suite)


@lineage_job(
    name="orders-bronze-to-silver",
    namespace="demo",
    lineage_provider="openlineage",
)
def run(get_storage):
    bronze = get_storage("bronze")
    silver = get_storage("silver")

    src = bronze.read(SOURCE_PATH)
    logger.info("Read %d bytes from bronze/%s", len(src.data), SOURCE_PATH)

    df = pl.read_csv(src.data)

    dq_validator = build_validator()
    result = dq_validator.validate(df)
    src.validate(result)
    result.raise_on_errors()

    df_clean = df.filter(
        pl.col("customer_id").is_not_null()
        & (pl.col("amount") > 0)
        & (pl.col("country").str.len_chars() == 2)
    ).with_columns(
        (pl.col("amount") * 1.19).round(2).alias("amount_incl_vat")
    )
    print(f"\nDropped {len(df) - len(df_clean)} invalid rows, kept {len(df_clean)}")

    silver.write(TARGET_PATH, df_clean.write_csv().encode()) \
        .validate(result) \
        .describe(
            order_id        = "Unique order identifier",
            customer_id     = "Customer identifier",
            amount          = "Order amount in EUR",
            country         = "ISO 3166-1 alpha-2 country code",
            status          = "Order fulfillment status",
            amount_incl_vat = "Amount including 19% VAT",
        )
    logger.info("Wrote to silver/%s", TARGET_PATH)


if __name__ == "__main__":
    import os
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    run()
