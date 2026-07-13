#!/usr/bin/env python3
import polars as pl

from steve_cli.decorators import lineage_job
from steve_cli.lineage.storage import GetStorage
from steve_cli.validation.adapters.validoopsie import ValidoopsieAdapter


SOURCE_PATH = "demo/orders.csv"
TARGET_PATH = "demo/orders_clean.csv"


VALIDATOR = ValidoopsieAdapter(suite=[
    ("warn", lambda vd: vd.NullValidation.ColumnNotBeNull("customer_id")),
    ("warn",  lambda vd: vd.ValuesValidation.ColumnValuesToBeBetween("amount", min_value=0.01)),
    ("info",  lambda vd: vd.StringValidation.LengthToBeEqualTo("country", value=2)),
])


@lineage_job()
def run(get_storage: GetStorage):
    bronze = get_storage("bronze")
    silver = get_storage("silver")

    df = bronze.read_csv(SOURCE_PATH, validator=VALIDATOR)

    df_clean = df.filter(
        pl.col("customer_id").is_not_null()
        & (pl.col("amount") > 0)
        & (pl.col("country").str.len_chars() == 2)
    ).with_columns(
        (pl.col("amount") * 1.19).round(2).alias("amount_incl_vat")
    )
    print(f"\nDropped {len(df) - len(df_clean)} invalid rows, kept {len(df_clean)}")

    silver.write_csv(TARGET_PATH, df_clean).describe(
            order_id        = "Unique order identifier",
            customer_id     = "Customer identifier",
            amount          = "Order amount in EUR",
            country         = "ISO 3166-1 alpha-2 country code",
            status          = "Order fulfillment status",
            amount_incl_vat = "Amount including 19% VAT",
        )
