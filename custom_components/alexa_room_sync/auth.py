"""Authentication adapters for Alexa Room Sync."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from .const import ALEXA_MEDIA_DOMAIN


def alexa_media_accounts(hass: HomeAssistant) -> dict[str, str]:
    """Return loaded Alexa Media Player entries with a usable login object."""
    accounts: dict[str, str] = {}
    for entry in hass.config_entries.async_entries(ALEXA_MEDIA_DOMAIN):
        if entry.state is not config_entries.ConfigEntryState.LOADED:
            continue
        runtime_data = getattr(entry, "runtime_data", None)
        if getattr(runtime_data, "login_obj", None) is None:
            continue
        accounts[entry.entry_id] = entry.title
    return accounts


def get_alexa_media_login(
    hass: HomeAssistant, entry_id: str | None = None
) -> tuple[str, Any] | None:
    """Resolve one loaded Alexa Media Player login object."""
    accounts = alexa_media_accounts(hass)
    if entry_id is None and len(accounts) == 1:
        entry_id = next(iter(accounts))
    if entry_id not in accounts:
        return None
    entry = hass.config_entries.async_get_entry(entry_id)
    runtime_data = getattr(entry, "runtime_data", None) if entry else None
    login_obj = getattr(runtime_data, "login_obj", None)
    if login_obj is None:
        return None
    return entry_id, login_obj
