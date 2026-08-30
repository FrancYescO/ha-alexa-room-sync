"""Alexa API parsing safety tests."""

from __future__ import annotations

import asyncio

from custom_components.alexa_room_sync.api import AlexaRoomApi


def test_description_entity_id_wins_over_opaque_serial() -> None:
    api = AlexaRoomApi(None, "https://eu-api-alexa.amazon.it", None)

    async def _graphql(*args, **kwargs):
        return {
            "endpoints": {
                "items": [
                    {
                        "endpointId": "endpoint",
                        "friendlyName": "Lampada",
                        "serialNumber": {"value": {"text": "opaque-serial"}},
                        "description": {
                            "value": {"text": "light.tavolo via Home Assistant"}
                        },
                        "manufacturer": {"value": {"text": "Home Assistant"}},
                        "legacyAppliance": {
                            "applianceId": "appliance",
                            "driverIdentity": {"namespace": "SKILL"},
                        },
                    }
                ]
            }
        }

    api._graphql = _graphql

    endpoints = asyncio.run(api.async_list_endpoints())

    assert endpoints[0].source_entity_id == "light.tavolo"
