"""Load and validate events.json (schema frozen in Agents.md)."""

import json
from dataclasses import dataclass
from pathlib import Path

EVENT_TYPES = {"footstep", "impact", "hit", "door", "pickup", "ui", "whoosh", "other"}
MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Event:
    id: str
    t: float
    duration: float
    type: str
    prompt: str
    volume: float
    pan: float
    confidence: float


@dataclass(frozen=True)
class Ambience:
    prompt: str
    volume: float


@dataclass(frozen=True)
class EventsFile:
    video_id: str
    duration: float
    fps: float
    events: list[Event]
    ambience: Ambience | None

    def audible_events(self) -> list[Event]:
        """Events Audio should play: confidence 0.5 and above, in time order."""
        return sorted((e for e in self.events if e.confidence >= MIN_CONFIDENCE), key=lambda e: e.t)


def load_events(path: str | Path) -> EventsFile:
    with open(path) as f:
        return parse_events(json.load(f))


def parse_events(data: dict) -> EventsFile:
    video = _require(data, "video", dict, "events.json")
    duration = _number(video, "duration", "video")
    if duration <= 0:
        raise ValueError(f"video.duration must be > 0, got {duration}")

    events = []
    for i, raw in enumerate(_require(data, "events", list, "events.json")):
        where = f"events[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object")
        event = Event(
            id=str(_require(raw, "id", str, where)),
            t=_number(raw, "t", where),
            duration=_number(raw, "duration", where),
            type=_require(raw, "type", str, where),
            prompt=_require(raw, "prompt", str, where).strip(),
            volume=_number(raw, "volume", where),
            pan=_number(raw, "pan", where),
            confidence=_number(raw, "confidence", where),
        )
        _check_event(event, f"{where} ({event.id})")
        events.append(event)

    ambience = None
    if data.get("ambience") is not None:
        raw = _require(data, "ambience", dict, "events.json")
        ambience = Ambience(
            prompt=_require(raw, "prompt", str, "ambience").strip(),
            volume=_number(raw, "volume", "ambience"),
        )
        _check_range(ambience.volume, 0, 1, "ambience.volume")

    return EventsFile(
        video_id=str(video.get("id", "")),
        duration=duration,
        fps=float(video.get("fps", 30)),
        events=events,
        ambience=ambience,
    )


def _check_event(e: Event, where: str) -> None:
    if e.type not in EVENT_TYPES:
        raise ValueError(f"{where}: type {e.type!r} is not one of {sorted(EVENT_TYPES)}")
    if not e.prompt:
        raise ValueError(f"{where}: prompt is empty")
    if e.t < 0:
        raise ValueError(f"{where}: t must be >= 0, got {e.t}")
    if e.duration <= 0:
        raise ValueError(f"{where}: duration must be > 0, got {e.duration}")
    _check_range(e.volume, 0, 1, f"{where}: volume")
    _check_range(e.pan, -1, 1, f"{where}: pan")
    _check_range(e.confidence, 0, 1, f"{where}: confidence")


def _check_range(value: float, lo: float, hi: float, name: str) -> None:
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}, got {value}")


def _require(obj: dict, key: str, kind: type, where: str):
    if key not in obj:
        raise ValueError(f"{where} is missing {key!r}")
    if not isinstance(obj[key], kind):
        raise ValueError(f"{where}.{key} must be {kind.__name__}")
    return obj[key]


def _number(obj: dict, key: str, where: str) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}.{key} must be a number")
    return float(value)
