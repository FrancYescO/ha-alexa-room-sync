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
