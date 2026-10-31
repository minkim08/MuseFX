import copy
import json

import pytest

from musefx_audio.events import load_events, parse_events


def test_sample_fixture_loads_and_skips_low_confidence(sample_events):
    events_file = load_events(sample_events)
    assert len(events_file.events) == 10
    audible = events_file.audible_events()
    assert [e.id for e in audible] == ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9"]
    assert events_file.ambience.volume == 0.3


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("type", "explosion", "type"),
        ("pan", 1.5, "pan"),
        ("volume", -0.1, "volume"),
        ("duration", 0, "duration"),
        ("t", "soon", "t must be a number"),
    ],
)
def test_contract_violations_are_rejected(sample_events, field, value, message):
    data = json.loads(sample_events.read_text())
    bad = copy.deepcopy(data)
    bad["events"][0][field] = value
    with pytest.raises(ValueError, match=message):
        parse_events(bad)


def test_missing_field_is_rejected(sample_events):
    data = json.loads(sample_events.read_text())
    del data["events"][0]["prompt"]
    with pytest.raises(ValueError, match="prompt"):
        parse_events(data)


def test_ambience_is_optional(sample_events):
    data = json.loads(sample_events.read_text())
    del data["ambience"]
    assert parse_events(data).ambience is None
