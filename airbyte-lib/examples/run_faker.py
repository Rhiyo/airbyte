# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
"""A simple test of AirbyteLib, using the Faker source connector.

Usage (from airbyte-lib root directory):
> poetry run python ./examples/run_faker.py

No setup is needed, but you may need to delete the .venv-source-faker folder
if your installation gets interrupted or corrupted.
"""
from __future__ import annotations

import airbyte_lib as ab


SCALE = 5_000_000  # Number of records to generate between users and purchases.


source = ab.get_connector(
    "source-faker",
    config={"count": SCALE / 2},
    install_if_missing=True,
)
source.check()
source.set_streams(["products", "users", "purchases"])

result = source.read()

for name, records in result.streams.items():
    print(f"Stream {name}: {len(records)} records")
