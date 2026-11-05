"""Debounced, opt-in registry synchronization with an hourly reconciliation."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .api import AlexaApiError
from .const import EVENT_SYNC_FINISHED

_LOGGER = logging.getLogger(__name__)


def async_start(hass, entry):
    pending = []
    started = []
    stopped = False
    running = set()

    async def run(_now=None):
        if stopped:
            return
        for cancel in pending:
            cancel()
        pending.clear()
        runtime = entry.runtime_data
        if runtime.options.get("dry_run", True) or not runtime.options.get(
            "allow_writes"
        ):
            return
        task = asyncio.current_task()
        running.add(task)
        try:
            result = await runtime.async_apply(hass, automatic=True)
            if result["applied_change_count"]:
                hass.bus.async_fire(EVENT_SYNC_FINISHED, result)
        except (AlexaApiError, ValueError):
            _LOGGER.warning(
                "Automatic Alexa room sync failed; inspect the last result and preview again"
            )
        finally:
            running.discard(task)

    @callback
    def schedule(_event=None):
        if stopped:
            return
        for cancel in pending:
            cancel()
        pending.clear()
        pending.append(async_call_later(hass, 60, run))

    unsubs = [
        hass.bus.async_listen(name, schedule)
        for name in (
            "area_registry_updated",
            "floor_registry_updated",
            "device_registry_updated",
            "entity_registry_updated",
        )
    ]
    unsubs.append(async_track_time_interval(hass, run, timedelta(hours=1)))

    @callback
    def on_started(_event):
        started.clear()
        schedule()

    if hass.is_running:
        schedule()
    else:
        started.append(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, on_started)
        )

    @callback
    def stop():
        nonlocal stopped
        stopped = True
        for cancel in pending + started + unsubs:
            cancel()
        pending.clear()
        started.clear()
        unsubs.clear()
        for task in running:
            task.cancel()

    return stop
