"""Private Alexa GraphQL client.

These operations were observed in the Alexa mobile app in July 2026. They are
not a public Amazon API and can change without notice.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import re
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientSession

from .const import UPDATE_ADD, UPDATE_REMOVE
from .models import AlexaEndpoint, AlexaGroup

LIST_ENDPOINTS_QUERY = """
query AlexaRoomSyncEndpoints {
  endpoints(endpointsQueryParams: { paginationParams: { disablePagination: true } }) {
    items {
      endpointId
      id
      displayCategories { primary { value } }
      friendlyName
      friendlyNameObject { value { text } }
      model { value { text } }
      serialNumber { value { text } }
      description { value { text } }
      manufacturer { value { text } }
      isEnabled
      legacyAppliance {
        applianceId
        friendlyDescription
        manufacturerName
        modelName
        isEnabled
        driverIdentity
      }
    }
  }
}
"""

LIST_GROUPS_QUERY = """
query listDeviceGroupsDocumentNode {
  listDeviceGroups {
    deviceGroups {
      id
      friendlyName { value { text } }
      memberDevices { items { id associatedUnits { id } } }
      childDeviceGroups { id }
    }
  }
}
"""

UPDATE_GROUP_MUTATION = """
mutation UpdateDeviceGroup($deviceGroupId: String!, $memberDeviceIds: [String!], $memberDeviceIdsUpdateOperation: CollectionOperationOptions!) {
  updateDeviceGroup(updateDeviceGroupInput: {
    deviceGroupId: $deviceGroupId,
    memberDeviceIdsUpdateOperation: $memberDeviceIdsUpdateOperation,
    memberDeviceIds: $memberDeviceIds
  }) {
    deviceGroup {
      id
      friendlyName { value { text } }
      memberDevices { items { id associatedUnits { id } } }
    }
  }
}
"""

CREATE_GROUP_MUTATION = """
mutation CreateDeviceGroup($friendlyName: String!) {
  createDeviceGroup(createDeviceGroupInput: {
    friendlyName: $friendlyName
  }) {
    __typename
  }
}
"""

CAPABILITIES_QUERY = """
query AlexaRoomSyncCapabilities {
  __schema {
    mutationType {
      fields {
        name
        args {
          name
          type {
            kind
            name
            ofType {
              kind
              name
              ofType { kind name }
            }
          }
        }
      }
    }
  }
  createDeviceGroupInput: __type(name: "CreateDeviceGroupInput") {
    name
    inputFields {
      name
      type {
        kind
        name
        ofType {
          kind
          name
          inputFields {
            name
            type {
              kind
              name
              ofType { kind name }
            }
          }
        }
      }
    }
  }
}
"""


class AlexaApiError(Exception):
    """Raised when Alexa rejects or cannot process a request."""


class AlexaAuthError(AlexaApiError):
    """Raised when the captured Amazon session has expired."""


class AlexaRoomApi:
    """Minimal client for the Alexa room GraphQL calls."""

    def __init__(
        self,
        session: ClientSession | None,
        host: str,
        cookie: str | None,
        extra_headers: Mapping[str, str] | None = None,
        *,
        session_provider: Callable[[], ClientSession] | None = None,
        cookies_provider: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._session = session
        self._session_provider = session_provider
        self._cookies_provider = cookies_provider
        self._url = f"{host.rstrip('/')}/nexus/v1/graphql"
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        if cookie:
            self._headers["Cookie"] = cookie

    @classmethod
    def from_alexa_media(
        cls,
        login_obj: Any,
        host: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> AlexaRoomApi:
        """Use Alexa Media Player's live authenticated session."""

        async def _cookies() -> Any:
            return await login_obj.load_cookie()

        return cls(
            None,
            host,
            None,
            extra_headers,
            session_provider=lambda: login_obj.session,
            cookies_provider=_cookies,
        )

    async def _graphql(
        self, operation_name: str, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = {
            "operationName": operation_name,
            "query": query,
            "variables": variables or {},
        }
        session = self._session_provider() if self._session_provider else self._session
        if session is None or session.closed:
            raise AlexaAuthError("La sessione Alexa non è disponibile")
        cookies = (
            await self._cookies_provider() if self._cookies_provider else None
        )
        try:
            async with session.post(
                self._url,
                json=payload,
                headers=self._headers,
                cookies=cookies,
                timeout=30,
            ) as response:
                if response.status in (401, 403):
                    raise AlexaAuthError("La sessione Amazon/Alexa è scaduta")
                response.raise_for_status()
                result: Any = await response.json(content_type=None)
        except AlexaApiError:
            raise
        except (ClientError, TimeoutError, ValueError) as err:
            raise AlexaApiError(f"Richiesta Alexa fallita: {err}") from err

        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, Mapping):
            raise AlexaApiError("Risposta GraphQL non valida")
        if result.get("errors"):
            messages = "; ".join(
                str(item.get("message", "Errore GraphQL"))
                for item in result["errors"]
            )
            if "auth" in messages.casefold() or "unauthor" in messages.casefold():
                raise AlexaAuthError(messages)
            raise AlexaApiError(messages)
        data = result.get("data")
        if not isinstance(data, dict):
            raise AlexaApiError("Risposta GraphQL priva del campo data")
        return data

    async def async_list_endpoints(self) -> list[AlexaEndpoint]:
        """Return enabled Alexa endpoints."""
        data = await self._graphql(
            "AlexaRoomSyncEndpoints", LIST_ENDPOINTS_QUERY
        )
        raw_items = data.get("endpoints", {}).get("items", [])
        endpoints: list[AlexaEndpoint] = []
        for item in raw_items:
            legacy = item.get("legacyAppliance") or {}
            if item.get("isEnabled") is False or legacy.get("isEnabled") is False:
                continue
            friendly_name = item.get("friendlyNameObject") or {}
            friendly_name_value = friendly_name.get("value") or {}
            display_categories = item.get("displayCategories") or {}
            primary_category = display_categories.get("primary") or {}
            model = item.get("model") or {}
            model_value = model.get("value") or {}
            serial_number = item.get("serialNumber") or {}
            serial_number_value = serial_number.get("value") or {}
            description = item.get("description") or {}
            description_value = description.get("value") or {}
            manufacturer = item.get("manufacturer") or {}
            manufacturer_value = manufacturer.get("value") or {}
            driver_identity = legacy.get("driverIdentity") or {}
            name = item.get("friendlyName") or friendly_name_value.get("text")
            endpoint_id = item.get("endpointId") or item.get("id")
            if not name or not endpoint_id:
                continue
            description_text = (
                description_value.get("text") or legacy.get("friendlyDescription")
            )
            serial_text = serial_number_value.get("text")
            description_entity_id = _entity_id_from_description(description_text)
            endpoints.append(
                AlexaEndpoint(
                    endpoint_id=endpoint_id,
                    name=name,
                    category=primary_category.get("value"),
                    model=model_value.get("text") or legacy.get("modelName"),
                    # A Home Assistant description is stronger evidence than
                    # an opaque Alexa serial number. MatterHub endpoints still
                    # fall back to their entity_id-shaped serial.
                    source_entity_id=description_entity_id or serial_text,
                    manufacturer=(
                        manufacturer_value.get("text")
                        or legacy.get("manufacturerName")
                    ),
                    description=description_text,
                    source_provider=driver_identity.get("namespace"),
                    appliance_id=legacy.get("applianceId"),
                )
            )
        return endpoints

    async def async_delete_appliance(self, appliance_id: str) -> None:
        """Delete one legacy Alexa smart-home appliance."""
        session = self._session_provider() if self._session_provider else self._session
        if session is None or session.closed:
            raise AlexaAuthError("La sessione Alexa non è disponibile")
        cookies = await self._cookies_provider() if self._cookies_provider else None
        headers = dict(self._headers)
        csrf = _csrf_cookie(cookies)
        if csrf:
            headers["csrf"] = csrf
        url = f"{self._url.rsplit('/nexus/v1/graphql', 1)[0]}/api/phoenix/appliance/{quote(appliance_id, safe='')}"
        try:
            async with session.delete(
                url, headers=headers, cookies=cookies, timeout=30
            ) as response:
                if response.status in (401, 403):
                    raise AlexaAuthError("La sessione Amazon/Alexa è scaduta")
                if response.status >= 400:
                    body = (await response.text())[:300]
                    raise AlexaApiError(
                        f"Eliminazione Alexa rifiutata ({response.status}): {body}"
                    )
        except AlexaApiError:
            raise
        except (ClientError, TimeoutError, ValueError) as err:
            raise AlexaApiError(f"Eliminazione Alexa fallita: {err}") from err

    async def async_list_groups(self) -> list[AlexaGroup]:
        """Return Alexa groups and their direct members."""
        data = await self._graphql(
            "listDeviceGroupsDocumentNode", LIST_GROUPS_QUERY
        )
        raw_items = data.get("listDeviceGroups", {}).get("deviceGroups", [])
        groups: list[AlexaGroup] = []
        for item in raw_items:
            friendly_name = item.get("friendlyName") or {}
            friendly_name_value = friendly_name.get("value") or {}
            name = friendly_name_value.get("text")
            group_id = item.get("id")
            if not name or not group_id:
                continue
            member_devices = item.get("memberDevices") or {}
            members = member_devices.get("items") or []
            groups.append(
                AlexaGroup(
                    group_id=group_id,
                    name=name,
                    member_ids=frozenset(
                        member["id"] for member in members if member.get("id")
                    ),
                )
            )
        return groups

    async def async_update_group(
        self, group_id: str, endpoint_id: str, operation: str
    ) -> None:
        """Add or remove one endpoint from one Alexa group."""
        if operation not in (UPDATE_ADD, UPDATE_REMOVE):
            raise ValueError(f"Operazione non valida: {operation}")
        await self._graphql(
            "UpdateDeviceGroup",
            UPDATE_GROUP_MUTATION,
            {
                "deviceGroupId": group_id,
                "memberDeviceIds": [endpoint_id],
                "memberDeviceIdsUpdateOperation": operation,
            },
        )

    async def async_create_group(self, friendly_name: str) -> None:
        """Create an empty Alexa device group."""
        await self._graphql(
            "CreateDeviceGroup",
            CREATE_GROUP_MUTATION,
            {"friendlyName": friendly_name},
        )

    async def async_group_capabilities(self) -> dict[str, Any]:
        """Return group-related GraphQL mutations exposed by Alexa."""
        data = await self._graphql(
            "AlexaRoomSyncCapabilities", CAPABILITIES_QUERY
        )
        fields = (
            data.get("__schema", {})
            .get("mutationType", {})
            .get("fields", [])
        )
        return {
            "group_mutations": [
                item
                for item in fields
                if "group" in str(item.get("name", "")).casefold()
            ],
            "create_input": data.get("createDeviceGroupInput"),
        }


def _entity_id_from_description(value: Any) -> str | None:
    """Extract an HA entity ID from Alexa's Home Assistant description."""
    if not isinstance(value, str):
        return None
    match = re.search(r"\b([a-z_]+\.[a-z0-9_]+)\s+via Home Assistant\b", value)
    return match.group(1) if match else None


def _csrf_cookie(cookies: Any) -> str | None:
    """Best-effort extraction of Amazon's CSRF cookie."""
    if cookies is None:
        return None
    if isinstance(cookies, Mapping):
        value = cookies.get("csrf")
        return getattr(value, "value", value) if value else None
    try:
        for item in cookies:
            if getattr(item, "key", None) == "csrf":
                return getattr(item, "value", None)
    except TypeError:
        return None
    return None
