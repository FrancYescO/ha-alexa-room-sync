import asyncio
from types import SimpleNamespace as NS

from custom_components.alexa_room_sync import cloud


def test_missing_cloud_entities_wait_ten_minutes_and_respect_hourly_limit(monkeypatch):
    now = [0]
    monkeypatch.setattr(cloud.time, "monotonic", lambda: now[0])
    runtime = NS(missing_since={}, last_resend=None)
    hass = NS(data={})
    first = asyncio.run(
        cloud.async_resend_missing(hass, runtime, {"light.lost": "Lost"})
    )
    assert first["status"] == "waiting"
    assert runtime.last_resend is None
    runtime.last_resend = 0
    now[0] = 700
    assert (
        asyncio.run(cloud.async_resend_missing(hass, runtime, {"light.lost": "Lost"}))[
            "status"
        ]
        == "rate_limited"
    )
    runtime.last_resend = None
    # A returning device clears its timer even if a new missing device is present.
    asyncio.run(cloud.async_resend_missing(hass, runtime, {"light.new": "New"}))
    assert runtime.missing_since == {"light.new": 700}


def test_cloud_resend_uses_only_add_or_update_and_is_rate_limited(monkeypatch):
    import sys
    from types import ModuleType

    now = [0]
    monkeypatch.setattr(cloud.time, "monotonic", lambda: now[0])
    calls = []

    async def report(hass, config, entity_ids):
        calls.append(entity_ids)
        return NS(status=202)

    async def token():
        return "not-a-real-token"

    config = NS(enabled=True, async_get_access_token=token)

    async def get_config():
        return config

    alexa = ModuleType("homeassistant.components.alexa")
    alexa.state_report = NS(async_send_add_or_update_message=report)
    monkeypatch.setitem(sys.modules, "homeassistant.components.alexa", alexa)
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.cloud.const", NS(DATA_CLOUD="cloud")
    )
    hass = NS(
        data={"cloud": NS(is_logged_in=True, client=NS(get_alexa_config=get_config))}
    )
    runtime = NS(missing_since={}, last_resend=None)
    missing = {"light.lost": "Lost"}
    assert (
        asyncio.run(cloud.async_resend_missing(hass, runtime, missing))["status"]
        == "waiting"
    )
    now[0] = 600
    assert asyncio.run(cloud.async_resend_missing(hass, runtime, missing)) == {
        "status": "resent",
        "entity_ids": ["light.lost"],
    }
    assert calls == [["light.lost"]]
    now[0] = 700
    assert (
        asyncio.run(cloud.async_resend_missing(hass, runtime, missing))["status"]
        == "rate_limited"
    )
    assert calls == [["light.lost"]]
