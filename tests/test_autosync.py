"""Debounce, startup lifecycle and write gates using a deterministic scheduler."""

import asyncio
import importlib
import sys
from types import ModuleType
from types import SimpleNamespace as NS


def setup(monkeypatch, started=True):
    monkeypatch.setattr(
        sys.modules["homeassistant.core"], "callback", lambda func: func, raising=False
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.const", NS(EVENT_HOMEASSISTANT_STARTED="started")
    )
    scheduled, intervals, listeners = [], [], {}
    events = ModuleType("homeassistant.helpers.event")

    def later(hass, delay, run):
        token = {"delay": delay, "run": run, "cancelled": False}
        scheduled.append(token)
        return lambda: token.update(cancelled=True)

    def interval(hass, run, delta):
        token = {"run": run, "delta": delta, "cancelled": False}
        intervals.append(token)
        return lambda: token.update(cancelled=True)

    events.async_call_later = later
    events.async_track_time_interval = interval
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.event", events)
    sys.modules.pop("custom_components.alexa_room_sync.autosync", None)
    autosync = importlib.import_module("custom_components.alexa_room_sync.autosync")
    calls = []

    async def apply(hass, **kwargs):
        calls.append(kwargs)
        return {"applied_change_count": 0}

    runtime = NS(options={"dry_run": False, "allow_writes": True}, async_apply=apply)

    def listen(name, callback):
        listeners[name] = callback
        return lambda: listeners.pop(name, None)

    hass = NS(
        is_running=started,
        bus=NS(
            async_listen=listen, async_listen_once=listen, async_fire=lambda *args: None
        ),
    )
    stop = autosync.async_start(hass, NS(runtime_data=runtime))
    return runtime, scheduled, intervals, listeners, calls, stop


def test_debounce_cancels_old_timers_and_unload_stops_every_listener(monkeypatch):
    _, timers, intervals, listeners, _, stop = setup(monkeypatch)
    assert timers[0]["delay"] == 60
    assert intervals[0]["delta"].total_seconds() == 3600
    listeners["area_registry_updated"](None)
    listeners["entity_registry_updated"](None)
    assert all(t["cancelled"] for t in timers[:-1])
    assert not timers[-1]["cancelled"]
    stop()
    assert timers[-1]["cancelled"] and intervals[0]["cancelled"]
    assert not listeners


def test_hourly_sync_respects_the_write_gates(monkeypatch):
    runtime, timers, intervals, _, calls, stop = setup(monkeypatch)
    runtime.options["dry_run"] = True
    asyncio.run(intervals[0]["run"]())
    assert not calls
    runtime.options["dry_run"] = False
    asyncio.run(intervals[0]["run"]())
    assert calls == [{"automatic": True}]
    assert timers[0]["cancelled"]
    stop()


def test_startup_listener_is_not_cancelled_after_it_already_fired(monkeypatch):
    _, timers, _, listeners, _, stop = setup(monkeypatch, started=False)
    assert not timers
    on_started = listeners.pop("started")  # HA removes a one-shot listener first
    on_started(None)
    assert len(timers) == 1
    stop()
    assert not listeners


def test_unload_cancels_an_already_running_automatic_sync(monkeypatch):
    import pytest

    runtime, _, intervals, _, _, stop = setup(monkeypatch)

    async def scenario():
        entered = asyncio.Event()

        async def apply(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        runtime.async_apply = apply
        task = asyncio.create_task(intervals[0]["run"]())
        await entered.wait()
        stop()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
