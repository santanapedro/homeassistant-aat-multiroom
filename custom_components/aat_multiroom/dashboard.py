"""Best-effort automatic Lovelace dashboard for AAT Multiroom.

Home Assistant has no *public* API for a custom integration to create or
update a dashboard - this uses the same internal mechanism Home Assistant
Core itself uses to auto-provision the built-in "Map" dashboard
(homeassistant/components/lovelace/__init__.py's `_create_map_dashboard`):
`hass.data["lovelace"]` holding a `dashboards_collection` (to create the
dashboard) and a `dashboards` mapping (each entry's own storage object,
used to write its views/cards).

Because this reaches into another component's internals (there's no
deprecation policy protecting it), every step here is defensive: any
failure is logged as a warning and swallowed. This feature is a
convenience layer on top of the integration, never a dependency of it -
zone control keeps working perfectly even if the dashboard step fails,
or if a future Home Assistant version changes this internal shape.

One such change already happened between HA versions: `hass.data["lovelace"]`
used to be a plain dict (`lovelace_data["dashboards_collection"]`) and later
became a typed object (`lovelace_data.dashboards_collection`). `_get_field`
tries attribute access first, then dict-style, so this keeps working across
both without needing to know which one a given HA version uses.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_INPUT_NAMES, CONF_ZONE_NAMES, DOMAIN
from .device import AatMultiroomDevice

EntityIdLookup = Callable[[str, str], "str | None"]

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL_PATH = "aat-multiroom"
DASHBOARD_TITLE = "AAT Multiroom"
DASHBOARD_ICON = "mdi:speaker-multiple"


def _get_field(container: Any, name: str) -> Any:
    """Read `name` off `container` whether it's a plain dict (older HA) or
    an attribute-based object (newer HA) - see the module docstring."""
    if container is None:
        return None
    value = getattr(container, name, None)
    if value is not None:
        return value
    if isinstance(container, dict):
        return container.get(name)
    return None


async def async_ensure_dashboard(
    hass: HomeAssistant, entry: ConfigEntry, device: AatMultiroomDevice
) -> None:
    """Create the shared AAT Multiroom dashboard if it doesn't exist yet,
    and add/update this entry's own view in it - one view per multiroom,
    so several units don't step on each other's layout."""
    try:
        await _async_ensure_dashboard(hass, entry, device)
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Could not create/update the automatic dashboard for %s "
            "(zone control itself is unaffected - you can still build "
            "a dashboard by hand using the media_player/switch/sensor "
            "entities directly)",
            entry.title,
            exc_info=True,
        )


async def _async_ensure_dashboard(
    hass: HomeAssistant, entry: ConfigEntry, device: AatMultiroomDevice
) -> None:
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        _LOGGER.debug("Lovelace isn't loaded; skipping the automatic dashboard")
        return

    dashboards_collection = _get_field(lovelace_data, "dashboards_collection")
    dashboards = _get_field(lovelace_data, "dashboards")
    if dashboards_collection is None or dashboards is None:
        _LOGGER.debug("Unexpected Lovelace data shape; skipping the automatic dashboard")
        return

    if DASHBOARD_URL_PATH not in dashboards:
        await dashboards_collection.async_create_item(
            {
                "icon": DASHBOARD_ICON,
                "title": DASHBOARD_TITLE,
                "url_path": DASHBOARD_URL_PATH,
            }
        )

    store = dashboards.get(DASHBOARD_URL_PATH)
    if store is None:
        _LOGGER.debug("Dashboard store missing right after creation; skipping")
        return

    ent_reg = er.async_get(hass)
    view = _build_view(ent_reg.async_get_entity_id, entry, device)
    if view is None:
        _LOGGER.debug("No entities found yet for %s; skipping dashboard view", entry.title)
        return

    current = await store.async_load(False)
    config: dict[str, Any] = current or {}
    views: list[dict[str, Any]] = config.setdefault("views", [])

    for i, existing_view in enumerate(views):
        if existing_view.get("path") == view["path"]:
            views[i] = view
            break
    else:
        views.append(view)

    await store.async_save(config)


def _build_view(
    lookup: EntityIdLookup, entry: ConfigEntry, device: AatMultiroomDevice
) -> dict[str, Any] | None:
    zone_names: dict[str, str] = entry.options.get(CONF_ZONE_NAMES, {})
    input_names: dict[str, str] = entry.options.get(CONF_INPUT_NAMES, {})
    input_numbers = sorted((int(key) for key in input_names))

    def entity_id(domain: str, unique_id: str) -> str | None:
        return lookup(domain, DOMAIN, unique_id)

    cards: list[dict[str, Any]] = []

    master_power = entity_id("switch", f"{entry.entry_id}_master_power")
    if master_power:
        cards.append({"type": "tile", "entity": master_power, "name": "Power geral"})

    for zone_num in sorted(device.zones):
        zone_name = zone_names.get(str(zone_num), f"Zona {zone_num}")
        media_player = entity_id("media_player", f"{entry.entry_id}_zone_{zone_num}_media_player")
        zone_power = entity_id("switch", f"{entry.entry_id}_zone_{zone_num}_power_switch")

        input_tiles: list[dict[str, Any]] = []
        for input_num in input_numbers:
            switch_id = entity_id(
                "switch", f"{entry.entry_id}_zone_{zone_num}_input_{input_num}_switch"
            )
            if switch_id:
                input_tiles.append(
                    {
                        "type": "tile",
                        "entity": switch_id,
                        "name": input_names.get(str(input_num), f"Entrada {input_num}"),
                    }
                )

        zone_cards: list[dict[str, Any]] = []
        if media_player:
            zone_cards.append({"type": "tile", "entity": media_player, "name": zone_name})

        row: list[dict[str, Any]] = []
        if zone_power:
            row.append(
                {"type": "tile", "entity": zone_power, "name": "Power", "icon": "mdi:power"}
            )
        row.extend(input_tiles)
        if row:
            zone_cards.append(
                {"type": "grid", "columns": min(len(row), 4), "square": False, "cards": row}
            )

        if zone_cards:
            cards.append({"type": "vertical-stack", "cards": zone_cards})

    if not cards:
        return None

    return {
        "path": f"multiroom-{entry.entry_id}",
        "title": entry.title,
        "icon": DASHBOARD_ICON,
        "cards": cards,
    }
