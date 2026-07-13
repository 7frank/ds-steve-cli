#!/usr/bin/env python3
import logging

import polars as pl

from steve_cli.decorators import lineage_job
from steve_cli.lineage.storage import GetStorage
from steve_cli.validation.adapters.validoopsie import ValidoopsieAdapter

logger = logging.getLogger(__name__)

SOURCE_PATH = "demo/orders.csv"
TARGET_PATH = "demo/orders_demo2.csv"


VALIDATOR = ValidoopsieAdapter(suite=[
    ("error", lambda vd: vd.NullValidation.ColumnNotBeNull("order_id")),
    ("warn",  lambda vd: vd.UniqueValidation.ColumnUniqueValuesToBeInList(
        "status", values=["completed", "pending", "cancelled"]
    )),
    ("warn",  lambda vd: vd.ValuesValidation.ColumnValuesToBeBetween("amount", max_value=1000.0)),
    ("info",  lambda vd: vd.UniqueValidation.ColumnUniqueValuesToBeInList(
        "country", values=["DE", "US", "FR", "GB"]
    )),
])


@lineage_job()
def run(get_storage: GetStorage):
    bronze = get_storage("bronze")
    silver = get_storage("silver")

    df = bronze.read_csv(SOURCE_PATH, validator=VALIDATOR)

    df_enriched = df.filter(
        pl.col("order_id").is_not_null()
        & (pl.col("amount") <= 1000.0)
    ).with_columns(
        pl.col("status").str.to_uppercase().alias("status_upper"),
        pl.col("amount").cast(pl.Float64).round(2),
    )
    print(f"\nDropped {len(df) - len(df_enriched)} invalid rows, kept {len(df_enriched)}")

    silver.write_csv(TARGET_PATH, df_enriched).describe(
            order_id     = "Unique order identifier",
            customer_id  = "Customer identifier",
            amount       = "Order amount in EUR (max 1000)",
            country      = "ISO 3166-1 alpha-2 country code",
            status       = "Order fulfillment status",
            status_upper = "Status in uppercase",
        )
