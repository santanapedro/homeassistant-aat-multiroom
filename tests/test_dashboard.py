"""Tests for the best-effort automatic-dashboard feature.

`_build_view` takes its entity lookup as an injected function precisely so
it can be tested here without touching Home Assistant's real entity
registry or Lovelace storage internals.

`async_ensure_dashboard`'s orchestration (create the dashboard if missing,
merge this entry's view into its stored views) is tested against fakes
standing in for Home Assistant's own `homeassistant.components.lovelace.
dashboard.DashboardsCollection` class and `homeassistant.helpers.storage.
Store` - not the real Lovelace subsystem, which is exactly the point:
this feature must degrade gracefully if these internals ever change
shape again (they already have once, live - see dashboard.py's module
docstring for the full story).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from custom_components.aat_multiroom.dashboard import (
    DASHBOARD_URL_PATH,
    _build_view,
    async_ensure_dashboard,
)
from custom_components.aat_multiroom.device import ZoneState


def make_entry(entry_id: str = "entry1"):
    return SimpleNamespace(
        entry_id=entry_id,
        title="AAT Multiroom PMR7",
        options={
            "zone_names": {"1": "Sala"},
            "input_names": {"1": "TV", "2": "Spotify"},
        },
    )


class FakeDevice:
    def __init__(self, zones: dict[int, ZoneState]) -> None:
        self.zones = zones


# ---------------------------------------------------------------------
# _build_view: pure logic, fake entity lookup
# ---------------------------------------------------------------------


def test_build_view_returns_none_when_no_entities_registered() -> None:
    view = _build_view(lambda *args: None, make_entry(), FakeDevice({1: ZoneState()}))
    assert view is None


def test_build_view_includes_master_power_and_zone_cards() -> None:
    registered = {
        ("switch", "aat_multiroom", "entry1_master_power"): "switch.master",
        ("media_player", "aat_multiroom", "entry1_zone_1_media_player"): "media_player.sala",
        ("switch", "aat_multiroom", "entry1_zone_1_power_switch"): "switch.sala_power",
        ("switch", "aat_multiroom", "entry1_zone_1_input_1_switch"): "switch.sala_tv",
        ("switch", "aat_multiroom", "entry1_zone_1_input_2_switch"): "switch.sala_spotify",
    }

    def lookup(domain: str, platform: str, unique_id: str) -> str | None:
        return registered.get((domain, platform, unique_id))

    view = _build_view(lookup, make_entry(), FakeDevice({1: ZoneState()}))

    assert view is not None
    assert view["path"] == "multiroom-entry1"
    assert view["title"] == "AAT Multiroom PMR7"

    # Master power tile, then one vertical-stack for zone 1.
    assert view["cards"][0] == {
        "type": "tile",
        "entity": "switch.master",
        "name": "Power geral",
    }
    zone_stack = view["cards"][1]
    assert zone_stack["type"] == "vertical-stack"
    media_tile, grid = zone_stack["cards"]
    assert media_tile == {"type": "tile", "entity": "media_player.sala", "name": "Sala"}
    assert grid["type"] == "grid"
    grid_entities = [c["entity"] for c in grid["cards"]]
    assert grid_entities == ["switch.sala_power", "switch.sala_tv", "switch.sala_spotify"]


def test_build_view_skips_entities_that_were_never_created() -> None:
    """Only the master power switch exists (e.g. a zone's own entities
    haven't finished registering yet) - the view should still degrade
    gracefully instead of crashing on missing entities."""

    def lookup(domain: str, platform: str, unique_id: str) -> str | None:
        if unique_id == "entry1_master_power":
            return "switch.master"
        return None

    view = _build_view(lookup, make_entry(), FakeDevice({1: ZoneState()}))

    assert view is not None
    assert view["cards"] == [{"type": "tile", "entity": "switch.master", "name": "Power geral"}]


def test_build_view_uses_generic_zone_and_input_names_as_fallback() -> None:
    entry = make_entry()
    entry.options = {}  # no zone/input names configured at all

    def lookup(domain: str, platform: str, unique_id: str) -> str | None:
        return {
            ("media_player", "aat_multiroom", "entry1_zone_1_media_player"): "media_player.zona_1",
        }.get((domain, platform, unique_id))

    view = _build_view(lookup, entry, FakeDevice({1: ZoneState()}))

    assert view is not None
    # No master power switch registered in this fake lookup, so cards[0] is
    # straight to zone 1's vertical-stack; its first card is the media tile.
    zone_stack = view["cards"][0]
    assert zone_stack["type"] == "vertical-stack"
    assert zone_stack["cards"][0]["name"] == "Zona 1"


# ---------------------------------------------------------------------
# async_ensure_dashboard: orchestration, against fakes for Home
# Assistant's own DashboardsCollection class and Store helper.
# ---------------------------------------------------------------------


class FakeStore:
    """Stands in for homeassistant.helpers.storage.Store: one instance per
    call, but all instances for the same key share the same backing dict
    (attached to `hass`), exactly like the real Store persists to the
    same file across separate instances pointed at the same key."""

    def __init__(self, hass, version, key) -> None:
        self.key = key
        self._backing = hass.data.setdefault("_fake_store_backing", {})

    async def async_load(self) -> dict | None:
        return self._backing.get(self.key)

    async def async_save(self, config: dict) -> None:
        self._backing[self.key] = config


class FakeDashboardsCollection:
    """Stands in for homeassistant.components.lovelace.dashboard.
    DashboardsCollection: a fresh instance per call (like the real one,
    which our code deliberately builds anew each time - see dashboard.py's
    module docstring), backed by a dict shared via `hass` so state
    persists across separate instances like real storage would."""

    def __init__(self, hass) -> None:
        self._storage: list[dict] = hass.data.setdefault("_fake_dashboards_storage", [])
        self._loaded: list[dict] = []
        self.created: list[dict] = []

    async def async_load(self) -> None:
        self._loaded = self._storage

    def async_items(self) -> list[dict]:
        return list(self._loaded)

    async def async_create_item(self, data: dict) -> dict:
        item = {"id": data["url_path"], **data}
        self.created.append(dict(data))
        self._storage.append(item)
        self._loaded = self._storage
        return item


def make_hass() -> SimpleNamespace:
    return SimpleNamespace(data={})


def patch_lovelace_internals(monkeypatch) -> None:
    """Point our module's imports at the fakes above instead of Home
    Assistant's real lovelace.dashboard module and storage.Store - both
    imported by name, so patching the real modules' attributes is enough
    even though our code imports them freshly on every call."""
    monkeypatch.setattr(
        "homeassistant.components.lovelace.dashboard.DashboardsCollection",
        FakeDashboardsCollection,
    )
    monkeypatch.setattr(
        "homeassistant.components.lovelace.dashboard.CONFIG_STORAGE_VERSION", 1
    )
    monkeypatch.setattr(
        "homeassistant.components.lovelace.dashboard.CONFIG_STORAGE_KEY", "lovelace.{}"
    )
    monkeypatch.setattr("custom_components.aat_multiroom.dashboard.Store", FakeStore)


async def test_ensure_dashboard_creates_dashboard_when_missing(monkeypatch) -> None:
    hass = make_hass()
    patch_lovelace_internals(monkeypatch)
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard.er.async_get",
        lambda hass: SimpleNamespace(async_get_entity_id=lambda *a: None),
    )
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard._build_view",
        lambda *a, **k: {"path": "multiroom-entry1", "title": "x", "cards": [{"type": "tile"}]},
    )

    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))

    assert hass.data["_fake_dashboards_storage"] == [
        {
            "id": DASHBOARD_URL_PATH,
            "icon": "mdi:speaker-multiple",
            "title": "AAT Multiroom",
            "url_path": DASHBOARD_URL_PATH,
        }
    ]
    saved = hass.data["_fake_store_backing"][f"lovelace.{DASHBOARD_URL_PATH}"]
    # Must match Home Assistant's own LovelaceStorage on-disk shape - the
    # views config wrapped one level deeper under "config" - or its
    # "lovelace/config" websocket command crashes reading it back (caught
    # live: KeyError('config') - see dashboard.py's module docstring).
    assert saved["config"]["views"][0]["path"] == "multiroom-entry1"


async def test_ensure_dashboard_does_not_recreate_existing_dashboard(monkeypatch) -> None:
    hass = make_hass()
    patch_lovelace_internals(monkeypatch)
    hass.data["_fake_dashboards_storage"] = [
        {"id": DASHBOARD_URL_PATH, "url_path": DASHBOARD_URL_PATH, "title": "AAT Multiroom"}
    ]
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard.er.async_get",
        lambda hass: SimpleNamespace(async_get_entity_id=lambda *a: None),
    )
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard._build_view",
        lambda *a, **k: {"path": "multiroom-entry1", "title": "x", "cards": []},
    )

    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))

    # Still just the one pre-existing entry - must not create a duplicate.
    assert len(hass.data["_fake_dashboards_storage"]) == 1


async def test_ensure_dashboard_updates_its_own_view_without_touching_others(monkeypatch) -> None:
    hass = make_hass()
    patch_lovelace_internals(monkeypatch)
    hass.data["_fake_dashboards_storage"] = [
        {"id": DASHBOARD_URL_PATH, "url_path": DASHBOARD_URL_PATH, "title": "AAT Multiroom"}
    ]
    other_view = {"path": "multiroom-other-entry", "title": "Other", "cards": []}
    old_view_for_this_entry = {"path": "multiroom-entry1", "title": "old title", "cards": []}
    hass.data["_fake_store_backing"] = {
        f"lovelace.{DASHBOARD_URL_PATH}": {
            "config": {"views": [other_view, old_view_for_this_entry]}
        }
    }

    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard.er.async_get",
        lambda hass: SimpleNamespace(async_get_entity_id=lambda *a: None),
    )
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard._build_view",
        lambda *a, **k: {"path": "multiroom-entry1", "title": "new title", "cards": []},
    )

    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))

    views = hass.data["_fake_store_backing"][f"lovelace.{DASHBOARD_URL_PATH}"]["config"]["views"]
    assert len(views) == 2
    assert other_view in views  # untouched
    assert {"path": "multiroom-entry1", "title": "new title", "cards": []} in views


async def test_ensure_dashboard_is_a_no_op_when_no_view_can_be_built(monkeypatch) -> None:
    """No entities registered yet -> _build_view returns None -> nothing
    gets written to the dashboard's own view storage. The shared "AAT
    Multiroom" dashboard shell may still get created (empty, no views) so
    it's ready the moment entities do show up - that's pre-existing
    behavior, not something this case changes."""
    hass = make_hass()
    patch_lovelace_internals(monkeypatch)
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard.er.async_get",
        lambda hass: SimpleNamespace(async_get_entity_id=lambda *a: None),
    )
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard._build_view", lambda *a, **k: None
    )

    # Must not raise.
    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))

    # No view storage was actually written to.
    assert hass.data.get("_fake_store_backing", {}) == {}


async def test_ensure_dashboard_is_a_no_op_when_lovelace_dashboard_module_is_missing(
    monkeypatch,
) -> None:
    """Regression-style coverage for the scenario that actually happened
    live: a future/different Home Assistant version could remove or
    relocate this module entirely. Simulated here by making the import
    fail outright."""
    monkeypatch.setitem(sys.modules, "homeassistant.components.lovelace.dashboard", None)

    # Must not raise.
    await async_ensure_dashboard(make_hass(), make_entry(), FakeDevice({1: ZoneState()}))


async def test_ensure_dashboard_swallows_unexpected_errors(monkeypatch) -> None:
    hass = make_hass()
    patch_lovelace_internals(monkeypatch)

    def _boom(*args, **kwargs):
        raise RuntimeError("Lovelace internals changed shape on some future HA version")

    monkeypatch.setattr("custom_components.aat_multiroom.dashboard.er.async_get", _boom)

    # Must not raise, even though _async_ensure_dashboard blew up internally.
    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))
