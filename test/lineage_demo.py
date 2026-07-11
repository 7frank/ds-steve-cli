#!/usr/bin/env python3
import logging

from steve_cli.decorators import lineage_job

logger = logging.getLogger(__name__)

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
        logger.info("Read %d bytes from bronze/%s", len(raw), SOURCE_PATH)
    except EnvironmentError:
        logger.warning("S3 not available, using built-in sample data")
        raw = SAMPLE_CSV

    lines = raw.decode().splitlines()
    header = lines[0]
    data_lines = [row for row in lines[1:] if row.split(",")[1].strip()]
    cleaned = (header + "\n" + "\n".join(data_lines) + "\n").encode()
    logger.info("Dropped %d rows with missing name", len(lines) - 1 - len(data_lines))

    result = validator.validate(None)
    logger.info("Validation: framework=%s, success=%s", result.framework, result.success)

    try:
        silver.put_bytes(cleaned, TARGET_PATH)
        logger.info("Wrote %d bytes to silver/%s", len(cleaned), TARGET_PATH)
    except EnvironmentError:
        logger.warning("S3 not available, skipping write")



if __name__ == "__main__":
    import os
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    run()
