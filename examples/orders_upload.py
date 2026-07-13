#!/usr/bin/env python3
import logging

from steve_cli.decorators import lineage_job

logger = logging.getLogger(__name__)

SOURCE_PATH = "demo/orders.csv"

SAMPLE_CSV = b"""order_id,customer_id,amount,country,status
1,101,49.99,DE,completed
2,102,0.00,US,completed
3,103,129.50,FR,completed
4,104,-5.00,DE,completed
5,,89.00,US,pending
6,106,200.00,XX,completed
7,107,59.99,DE,completed
"""


@lineage_job(
    name="orders-upload",
    namespace="demo",
    lineage_provider="openlineage",
)
def run(get_storage):
    bronze = get_storage("bronze")
    bronze.put_bytes(SAMPLE_CSV, SOURCE_PATH)
    logger.info("Uploaded sample orders to bronze/%s", SOURCE_PATH)


if __name__ == "__main__":
    import os
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    run()
