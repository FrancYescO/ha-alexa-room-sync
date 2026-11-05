"""Optional, rate-limited HA Cloud add-or-update reporting (no exposure toggles)."""

from __future__ import annotations

import time


async def async_resend_missing(hass, runtime, missing):
    now = time.monotonic()
    runtime.missing_since = {
        entity: runtime.missing_since.get(entity, now) for entity in missing
    }
    if runtime.last_resend is not None and now - runtime.last_resend < 3600:
        return {"status": "rate_limited", "entity_ids": []}
    due = [
        entity for entity, first in runtime.missing_since.items() if now - first >= 600
    ]
    if not due:
        return {"status": "waiting", "entity_ids": []}
    runtime.last_resend = now
    try:
        from homeassistant.components.alexa import state_report
        from homeassistant.components.cloud.const import DATA_CLOUD

        cloud = hass.data.get(DATA_CLOUD)
        if cloud is None or not cloud.is_logged_in:
            return {"status": "cloud_unavailable", "entity_ids": []}
        config = await cloud.client.get_alexa_config()
        if not config.enabled:
            return {"status": "alexa_disabled", "entity_ids": []}
        await config.async_get_access_token()
        response = await state_report.async_send_add_or_update_message(
            hass, config, due
        )
        if getattr(response, "status", 200) >= 400:
            return {"status": "request_failed", "entity_ids": []}
        return {"status": "resent", "entity_ids": due}
    except Exception:  # noqa: BLE001 - optional private API; report failure without breaking room sync
        # Private HA Cloud API failures do not invalidate successful room synchronization.
        return {"status": "request_failed", "entity_ids": []}
