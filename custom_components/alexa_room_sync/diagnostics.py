"""Diagnostics deliberately omit credentials, names, serials and account IDs."""

from collections import Counter


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    result = runtime.last_result
    return {
        "version": "0.9.0",
        "auth_method": entry.data.get("auth_method", "legacy"),
        "options": {
            key: value
            for key, value in runtime.options.items()
            if key not in {"group_prefix", "excluded_areas"}
        },
        "excluded_area_count": len(runtime.options.get("excluded_areas", [])),
        "managed_group_count": len(runtime.records),
        "pending_create_count": sum(
            bool(r.get("pending_create")) for r in runtime.records.values()
        ),
        "last_mode": result.get("mode"),
        "last_run": result.get("ran_at"),
        "last_change_count": result.get("change_count", 0),
        "last_applied_count": result.get("applied_change_count", 0),
        "blocked_reasons": dict(
            Counter(
                item.get("reason", "unknown") for item in result.get("ambiguous", [])
            )
        ),
    }
