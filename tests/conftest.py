"""Load pure integration modules without installing Home Assistant."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "alexa_room_sync"

custom_components = ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
package = ModuleType("custom_components.alexa_room_sync")
package.__path__ = [str(INTEGRATION)]

sys.modules.setdefault("custom_components", custom_components)
sys.modules.setdefault("custom_components.alexa_room_sync", package)


class ClientError(Exception):
    """Minimal aiohttp error stub for pure API tests."""


aiohttp = ModuleType("aiohttp")
aiohttp.ClientError = ClientError
aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", aiohttp)

homeassistant = ModuleType("homeassistant")
homeassistant.__path__ = []
core = ModuleType("homeassistant.core")
core.HomeAssistant = object
helpers = ModuleType("homeassistant.helpers")
helpers.__path__ = []

for registry_name in ("area_registry", "device_registry", "entity_registry"):
    registry = ModuleType(f"homeassistant.helpers.{registry_name}")
    registry.async_get = lambda hass: None
    setattr(helpers, registry_name, registry)
    sys.modules.setdefault(registry.__name__, registry)

sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.core", core)
sys.modules.setdefault("homeassistant.helpers", helpers)
