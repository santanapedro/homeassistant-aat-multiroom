"""Tests for the best-effort automatic-dashboard feature.

`_build_view` takes its entity lookup as an injected function precisely so
it can be tested here without touching Home Assistant's real entity
registry or Lovelace storage internals. `async_ensure_dashboard`'s
orchestration (create the dashboard if missing, merge this entry's view
into its stored views) is tested against small fakes standing in for
`dashboards_collection`/the dashboard's own store - not the real Lovelace
subsystem, which is exactly the point: this feature must degrade
gracefully if that internal shape ever changes.
"""

from __future__ import annotations

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
# async_ensure_dashboard: orchestration, against fake Lovelace internals
# ---------------------------------------------------------------------


class FakeStore:
    def __init__(self, initial: dict | None = None) -> None:
        self._data = initial
        self.saved: dict | None = None

    async def async_load(self, force: bool) -> dict | None:
        return self._data

    async def async_save(self, config: dict) -> None:
        self.saved = config
        self._data = config


class FakeDashboardsCollection:
    def __init__(self, dashboards: dict) -> None:
        self._dashboards = dashboards
        self.created: list[dict] = []

    async def async_create_item(self, data: dict) -> None:
        self.created.append(data)
        self._dashboards[data["url_path"]] = FakeStore()


def make_lovelace_hass_data() -> tuple[SimpleNamespace, dict, FakeDashboardsCollection]:
    dashboards: dict = {}
    collection = FakeDashboardsCollection(dashboards)
    hass = SimpleNamespace(data={"lovelace": {"dashboards_collection": collection, "dashboards": dashboards}})
    return hass, dashboards, collection


def make_lovelace_hass_data_attribute_style() -> tuple[SimpleNamespace, dict, FakeDashboardsCollection]:
    """Some Home Assistant versions store `hass.data["lovelace"]` as a
    plain dict (`make_lovelace_hass_data` above); others as an
    attribute-based object (e.g. a dataclass named `LovelaceData`) with
    the exact same field names. This reproduces a real bug caught live:
    `_get_field` must handle both without needing to know which one a
    given HA version uses."""
    dashboards: dict = {}
    collection = FakeDashboardsCollection(dashboards)
    lovelace_data = SimpleNamespace(dashboards_collection=collection, dashboards=dashboards)
    hass = SimpleNamespace(data={"lovelace": lovelace_data})
    return hass, dashboards, collection


async def test_ensure_dashboard_creates_dashboard_when_missing(monkeypatch) -> None:
    hass, dashboards, collection = make_lovelace_hass_data()
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard.er.async_get",
        lambda hass: SimpleNamespace(async_get_entity_id=lambda *a: None),
    )
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard._build_view",
        lambda *a, **k: {"path": "multiroom-entry1", "title": "x", "cards": [{"type": "tile"}]},
    )

    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))

    assert collection.created == [
        {
            "icon": "mdi:speaker-multiple",
            "title": "AAT Multiroom",
            "url_path": DASHBOARD_URL_PATH,
        }
    ]
    store = dashboards[DASHBOARD_URL_PATH]
    assert store.saved["views"][0]["path"] == "multiroom-entry1"


async def test_ensure_dashboard_works_when_lovelace_data_is_attribute_based(monkeypatch) -> None:
    """Regression test for a bug caught live: on some HA versions
    hass.data["lovelace"] is an attribute-based object, not a dict."""
    hass, dashboards, collection = make_lovelace_hass_data_attribute_style()
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard.er.async_get",
        lambda hass: SimpleNamespace(async_get_entity_id=lambda *a: None),
    )
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard._build_view",
        lambda *a, **k: {"path": "multiroom-entry1", "title": "x", "cards": [{"type": "tile"}]},
    )

    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))

    assert collection.created  # the dashboard got created despite the attribute-based container
    store = dashboards[DASHBOARD_URL_PATH]
    assert store.saved["views"][0]["path"] == "multiroom-entry1"


async def test_ensure_dashboard_does_not_recreate_existing_dashboard(monkeypatch) -> None:
    hass, dashboards, collection = make_lovelace_hass_data()
    dashboards[DASHBOARD_URL_PATH] = FakeStore({"views": []})
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard.er.async_get",
        lambda hass: SimpleNamespace(async_get_entity_id=lambda *a: None),
    )
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard._build_view",
        lambda *a, **k: {"path": "multiroom-entry1", "title": "x", "cards": []},
    )

    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))

    assert collection.created == []  # already existed, must not create again


async def test_ensure_dashboard_updates_its_own_view_without_touching_others(monkeypatch) -> None:
    hass, dashboards, _collection = make_lovelace_hass_data()
    other_view = {"path": "multiroom-other-entry", "title": "Other", "cards": []}
    old_view_for_this_entry = {"path": "multiroom-entry1", "title": "old title", "cards": []}
    dashboards[DASHBOARD_URL_PATH] = FakeStore({"views": [other_view, old_view_for_this_entry]})

    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard.er.async_get",
        lambda hass: SimpleNamespace(async_get_entity_id=lambda *a: None),
    )
    monkeypatch.setattr(
        "custom_components.aat_multiroom.dashboard._build_view",
        lambda *a, **k: {"path": "multiroom-entry1", "title": "new title", "cards": []},
    )

    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))

    views = dashboards[DASHBOARD_URL_PATH].saved["views"]
    assert len(views) == 2
    assert other_view in views  # untouched
    assert {"path": "multiroom-entry1", "title": "new title", "cards": []} in views


async def test_ensure_dashboard_is_a_no_op_when_lovelace_not_loaded() -> None:
    hass = SimpleNamespace(data={})  # no "lovelace" key at all

    # Must not raise.
    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))


async def test_ensure_dashboard_swallows_unexpected_errors(monkeypatch) -> None:
    hass, _dashboards, _collection = make_lovelace_hass_data()

    def _boom(*args, **kwargs):
        raise RuntimeError("Lovelace internals changed shape on some future HA version")

    monkeypatch.setattr("custom_components.aat_multiroom.dashboard.er.async_get", _boom)

    # Must not raise, even though _async_ensure_dashboard blew up internally.
    await async_ensure_dashboard(hass, make_entry(), FakeDevice({1: ZoneState()}))
