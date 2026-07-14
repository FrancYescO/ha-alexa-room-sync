"""Private Alexa GraphQL client.

These operations were observed in the Alexa mobile app in July 2026. They are
not a public Amazon API and can change without notice.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import UPDATE_ADD, UPDATE_REMOVE
from .models import AlexaEndpoint, AlexaGroup

LIST_ENDPOINTS_QUERY = """
query listEndpointsForGcFlow {
  listEndpoints(listEndpointsInput: {}) {
    endpoints {
      id
      displayCategories { primary { value } }
      friendlyNameObject { value { text } }
      model { value { text } }
      serialNumber { value { text } }
      enablement
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
        session: ClientSession,
        host: str,
        cookie: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._session = session
        self._url = f"{host.rstrip('/')}/nexus/v1/graphql"
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cookie": cookie,
            **(extra_headers or {}),
        }

    async def _graphql(
        self, operation_name: str, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = {
            "operationName": operation_name,
            "query": query,
            "variables": variables or {},
        }
        try:
            async with self._session.post(
                self._url, json=payload, headers=self._headers, timeout=30
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
            "listEndpointsForGcFlow", LIST_ENDPOINTS_QUERY
        )
        raw_items = data.get("listEndpoints", {}).get("endpoints", [])
        endpoints: list[AlexaEndpoint] = []
        for item in raw_items:
            if item.get("enablement") not in (None, "ENABLED"):
                continue
            friendly_name = item.get("friendlyNameObject") or {}
            friendly_name_value = friendly_name.get("value") or {}
            display_categories = item.get("displayCategories") or {}
            primary_category = display_categories.get("primary") or {}
            model = item.get("model") or {}
            model_value = model.get("value") or {}
            serial_number = item.get("serialNumber") or {}
            serial_number_value = serial_number.get("value") or {}
            name = friendly_name_value.get("text")
            endpoint_id = item.get("id")
            if not name or not endpoint_id:
                continue
            endpoints.append(
                AlexaEndpoint(
                    endpoint_id=endpoint_id,
                    name=name,
                    category=primary_category.get("value"),
                    model=model_value.get("text"),
                    source_entity_id=serial_number_value.get("text"),
                )
            )
        return endpoints

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
