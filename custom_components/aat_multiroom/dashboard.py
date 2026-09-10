"""Best-effort automatic Lovelace dashboard for AAT Multiroom.

Home Assistant has no *public* API for a custom integration to create or
update a dashboard. Two internal approaches were tried here, in sequence,
both discovered live against a real running instance:

1. Reaching into the *live* `hass.data["lovelace"]` object for its
   `dashboards_collection` - the same one Home Assistant Core itself uses
   to auto-provision the built-in "Map" dashboard. This broke outright on
   HA 2026.9.1: that attribute no longer exists on the live object at
   all - `dashboards_collection` became a local variable inside
   `lovelace`'s own `async_setup()`, with no hook left for anyone else to
   reach it. Confirmed by reading that version's actual source.
2. What this module does now: instantiate our *own*
   `homeassistant.components.lovelace.dashboard.DashboardsCollection`
   pointed at the exact same on-disk storage the live one uses (same
   Store key/version, imported from that module rather than hardcoded, so
   a future rename surfaces as an ImportError we catch, not a silent
   wrong-file write). This reads/writes the dashboard registry directly,
   independent of whatever shape the live in-memory object happens to
   have this version.

Trade-off accepted for approach 2: a *brand-new* dashboard we create this
way is on disk correctly, but won't appear in the sidebar until the next
Home Assistant restart - the panel/sidebar registration itself only
happens via the live collection's own listener during `lovelace`'s
`async_setup()`, which already ran before our config entry loaded. Once
the dashboard exists (after that first restart), updating its views
(renames, new zones, IP reconfiguration) applies immediately, with no
further restart - only the very first creation needs one.

Because this still reaches into another component's internals (there is
no deprecation policy protecting it, and it already changed shape once
between HA versions), every step here stays defensive: any failure is
logged as a warning and swallowed. This feature is a convenience layer on
top of the integration, never a dependency of it - zone control keeps
working perfectly even if the dashboard step fails, or if a future Home
Assistant version changes this internal shape yet again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import CONF_INPUT_NAMES, CONF_ZONE_NAMES, DOMAIN
from .device import AatMultiroomDevice

EntityIdLookup = Callable[[str, str], "str | None"]

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL_PATH = "aat-multiroom"
DASHBOARD_TITLE = "AAT Multiroom"
DASHBOARD_ICON = "mdi:speaker-multiple"


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
    try:
        # Imported lazily (and from the exact module that owns these
        # names) rather than at module load time: if a future Home
        # Assistant version renames/removes this module, the failure
        # surfaces here - inside our own try/except - instead of
        # crashing integration setup.
        from homeassistant.components.lovelace import dashboard as lovelace_dashboard
    except ImportError:
        _LOGGER.debug(
            "Home Assistant's lovelace.dashboard module isn't importable; "
            "skipping the automatic dashboard"
        )
        return

    # A fresh collection instance, unrelated to (and not synchronized
    # with) whatever live one `lovelace`'s own async_setup() built - but
    # pointed at the exact same on-disk storage (same Store key/version),
    # so reading/writing through it is equivalent to what the live one
    # would do.
    dashboards = lovelace_dashboard.DashboardsCollection(hass)
    await dashboards.async_load()

    existing_item = next(
        (item for item in dashboards.async_items() if item.get("url_path") == DASHBOARD_URL_PATH),
        None,
    )
    if existing_item is None:
        existing_item = await dashboards.async_create_item(
            {
                "icon": DASHBOARD_ICON,
                "title": DASHBOARD_TITLE,
                "url_path": DASHBOARD_URL_PATH,
            }
        )
        _LOGGER.info(
            "Created the automatic '%s' dashboard; it will appear in the "
            "sidebar after the next Home Assistant restart",
            DASHBOARD_TITLE,
        )

    dashboard_id = existing_item["id"]
    store: Store[dict[str, Any]] = Store(
        hass,
        lovelace_dashboard.CONFIG_STORAGE_VERSION,
        lovelace_dashboard.CONFIG_STORAGE_KEY.format(dashboard_id),
    )

    ent_reg = er.async_get(hass)
    view = _build_view(ent_reg.async_get_entity_id, entry, device)
    if view is None:
        _LOGGER.debug("No entities found yet for %s; skipping dashboard view", entry.title)
        return

    current = await store.async_load()
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
