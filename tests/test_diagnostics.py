import asyncio
import json
from types import SimpleNamespace as NS

from custom_components.alexa_room_sync.diagnostics import (
    async_get_config_entry_diagnostics,
)


def test_diagnostics_omit_credentials_names_serials_and_ids():
    runtime = NS(
        options={
            "group_prefix": "PRIVATE_NAME",
            "excluded_areas": ["PRIVATE_ID"],
            "dry_run": True,
        },
        records={"PRIVATE_ID": {"group_id": "PRIVATE_GROUP"}},
        last_result={
            "mode": "error",
            "endpoint_id": "PRIVATE_ENDPOINT",
            "serial": "PRIVATE_SERIAL",
        },
    )
    entry = NS(
        data={"cookie": "SECRET", "host": "PRIVATE_HOST", "auth_method": "cookie"},
        runtime_data=runtime,
    )
    result = asyncio.run(async_get_config_entry_diagnostics(None, entry))
    encoded = json.dumps(result)
    assert "SECRET" not in encoded and "PRIVATE" not in encoded
    assert result["managed_group_count"] == 1
    assert result["excluded_area_count"] == 1
