"""Tests for AatZoneMediaPlayer's read-only properties and source
selection - the entity layer had no direct coverage before (device.py and
api.py did, via test_device_state.py / test_api_protocol.py)."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.aat_multiroom.device import ZoneState
from custom_components.aat_multiroom.media_player import AatZoneMediaPlayer


class FakeDevice:
    model = "PMR7"
    connected = True
    signal = "aat_multiroom_test_update"

    def __init__(self, zones: dict[int, ZoneState] | None = None) -> None:
        self.zones = zones if zones is not None else {}
        self.selected: tuple[int, int] | None = None

    async def async_select_input(self, zone: int, input_num: int) -> None:
        self.selected = (zone, input_num)


def make_entry():
    return SimpleNamespace(
        entry_id="entry1",
        options={
            "zone_names": {"1": "Sala"},
            "input_names": {"1": "TV", "2": "Rádio", "3": "Spotify"},
        },
    )


def make_player(device: FakeDevice, zone_num: int = 1) -> AatZoneMediaPlayer:
    return AatZoneMediaPlayer(device, make_entry(), zone_num)


def test_source_and_media_title_reflect_current_input() -> None:
    device = FakeDevice({1: ZoneState(input=3, volume=10, mute=False, standby=False)})
    player = make_player(device)

    assert player.source == "Spotify"
    assert player.media_title == "Spotify"


def test_source_falls_back_to_generic_name_for_unnamed_input() -> None:
    device = FakeDevice({1: ZoneState(input=7, volume=10, mute=False, standby=False)})
    player = make_player(device)

    assert player.source == "Entrada 7"


def test_source_is_none_when_zone_unknown() -> None:
    player = make_player(FakeDevice({}))

    assert player.source is None
    assert player.media_title is None


def test_source_list_is_ordered_by_input_number() -> None:
    device = FakeDevice({1: ZoneState()})
    player = make_player(device)

    assert player.source_list == ["TV", "Rádio", "Spotify"]


async def test_async_select_source_maps_name_to_input_number() -> None:
    device = FakeDevice({1: ZoneState(input=1)})
    player = make_player(device)

    await player.async_select_source("Spotify")

    assert device.selected == (1, 3)


async def test_async_select_source_ignores_unknown_name() -> None:
    device = FakeDevice({1: ZoneState(input=1)})
    player = make_player(device)

    await player.async_select_source("Not A Real Input")

    assert device.selected is None
