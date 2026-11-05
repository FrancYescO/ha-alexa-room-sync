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


def test_cloud_appliance_identity_parses_compound_domains():
    from custom_components.alexa_room_sync.api import entity_id_from_alexa_id

    assert entity_id_from_alexa_id("opaque_prefix_light#kitchen") == "light.kitchen"
    assert (
        entity_id_from_alexa_id("opaque_prefix_input_boolean#test")
        == "input_boolean.test"
    )
    assert entity_id_from_alexa_id("opaque_prefix_unknown#test") is None
    assert entity_id_from_alexa_id("random-string") is None


def test_phoenix_bridge_requires_provable_identity():
    import pytest

    from custom_components.alexa_room_sync.api import AlexaApiError
    from custom_components.alexa_room_sync.models import AlexaEndpoint, AlexaGroup

    api = AlexaRoomApi(None, "https://eu-api-alexa.amazon.it", None)

    async def request(*args):
        return {
            "applianceGroups": [
                {"groupId": "phoenix", "name": "Kitchen", "applianceIds": ["appliance"]}
            ]
        }

    api._request = request
    group = AlexaGroup("graphql", "Kitchen", frozenset({"endpoint"}))
    endpoints = [AlexaEndpoint("endpoint", "Lamp", appliance_id="appliance")]
    assert asyncio.run(api._phoenix_group(group, endpoints))[0] == "phoenix"
    with pytest.raises(AlexaApiError):
        asyncio.run(api._phoenix_group(group, []))
    with pytest.raises(AlexaApiError):
        asyncio.run(
            api._phoenix_group(AlexaGroup("graphql", "Kitchen", frozenset()), endpoints)
        )


def test_phoenix_rename_preserves_document_and_manual_members():
    from custom_components.alexa_room_sync.models import AlexaGroup

    api = AlexaRoomApi(None, "https://eu-api-alexa.amazon.it", None)
    calls = []

    async def request(method, path, payload=None):
        calls.append((method, path, payload))
        return [
            {
                "groupId": "g",
                "name": "Old",
                "applianceIds": ["manual"],
                "unknownField": "keep",
            }
        ]

    api._request = request
    asyncio.run(api.async_rename_group(AlexaGroup("g", "Old", frozenset()), "New", []))
    assert calls[1][2] == {
        "groupId": "g",
        "name": "New",
        "applianceIds": ["manual"],
        "unknownField": "keep",
    }


def test_phoenix_delete_rechecks_members_even_when_group_ids_match():
    import pytest

    from custom_components.alexa_room_sync.api import AlexaApiError
    from custom_components.alexa_room_sync.models import AlexaEndpoint, AlexaGroup

    api = AlexaRoomApi(None, "https://eu-api-alexa.amazon.it", None)
    calls = []

    async def request(method, path, payload=None):
        calls.append(method)
        return [
            {"groupId": "g", "name": "Kitchen", "applianceIds": ["owned", "new-manual"]}
        ]

    api._request = request
    with pytest.raises(AlexaApiError):
        asyncio.run(
            api.async_delete_group(
                AlexaGroup("g", "Kitchen", frozenset({"endpoint"})),
                [AlexaEndpoint("endpoint", "Lamp", appliance_id="owned")],
            )
        )
    assert calls == ["GET"]


def test_alexa_devices_transport_uses_live_wrapper_and_app_host(monkeypatch):
    import sys
    from types import SimpleNamespace as NS

    calls = []

    class URL(str):
        def join(self, path):
            return self.rstrip("/") + str(path)

    monkeypatch.setitem(sys.modules, "yarl", NS(URL=URL))

    class Http:
        async def session_request(self, method, url, **kwargs):
            calls.append((method, str(url), kwargs))
            return None, NS(status=200)

        async def response_to_json(self, response, *args, **kwargs):
            return {"data": {"listDeviceGroups": {"deviceGroups": []}}}

    wrapper = Http()
    native = NS(
        _http_wrapper=wrapper,
        _session_state_data=NS(alexa_website_url="https://alexa.amazon.it"),
    )
    api = AlexaRoomApi.from_alexa_devices(
        lambda: native, "https://eu-api-alexa.amazon.it"
    )
    assert asyncio.run(api.async_list_groups()) == []
    assert calls[0][0:2] == ("POST", "https://eu-api-alexa.amazon.it/nexus/v1/graphql")
    assert calls[0][2]["json_data"] is True
    assert calls[0][2]["extended_headers"]["User-Agent"].startswith("AmazonWebView")


def test_alexa_devices_authentication_failure_never_looks_like_empty_inventory(
    monkeypatch,
):
    import sys
    from types import SimpleNamespace as NS

    import pytest

    from custom_components.alexa_room_sync.api import AlexaAuthError

    class URL(str):
        def join(self, path):
            return self + path

    monkeypatch.setitem(sys.modules, "yarl", NS(URL=URL))

    class Http:
        async def session_request(self, *args, **kwargs):
            return None, NS(status=401)

    api = AlexaRoomApi.from_alexa_devices(
        lambda: NS(_http_wrapper=Http()), "https://eu-api-alexa.amazon.it"
    )
    with pytest.raises(AlexaAuthError):
        asyncio.run(api.async_list_groups())


def test_malformed_inventory_is_an_error_instead_of_an_empty_list():
    import pytest

    from custom_components.alexa_room_sync.api import AlexaApiError

    api = AlexaRoomApi(None, "https://eu-api-alexa.amazon.it", None)

    async def malformed(*args, **kwargs):
        return {}

    api._graphql = malformed
    with pytest.raises(AlexaApiError):
        asyncio.run(api.async_list_endpoints())
    with pytest.raises(AlexaApiError):
        asyncio.run(api.async_list_groups())
