"""events.json contract: types, validation, and post-processing.

Mirrors the frozen contract in Agents.md and the semantic rules Product
enforces in product/backend/src/models.rs (validate()).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

EVENT_TYPES = {"footstep", "impact", "hit", "door", "pickup", "ui", "whoosh", "other"}

# Free-form model output mapped onto the fixed contract types.
TYPE_ALIASES = {
    "collision": "impact",
    "explosion": "impact",
    "crash": "impact",
    "step": "footstep",
    "click": "ui",
    "button": "ui",
    "pick-up": "pickup",
    "coin": "pickup",
    "swing": "whoosh",
    "swoosh": "whoosh",
}


def map_type(raw: str) -> str:
    t = (raw or "").strip().lower()
    if t in EVENT_TYPES:
        return t
    return TYPE_ALIASES.get(t, "other")


@dataclass
class Event:
    id: str
    t: float
    duration: float
    type: str
    prompt: str
    volume: float
    pan: float
    confidence: float


@dataclass
class EventsDocument:
    video_id: str
    duration: float
    fps: float
    events: list[Event] = field(default_factory=list)
    ambience: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "video": {"id": self.video_id, "duration": self.duration, "fps": self.fps},
            "events": [asdict(e) for e in self.events],
        }
        if self.ambience is not None:
            doc["ambience"] = self.ambience
        return doc


def validate(doc: dict[str, Any]) -> None:
    """Raise ValueError if doc violates the contract (mirrors Product)."""
    video = doc.get("video") or {}
    if not str(video.get("id") or ""):
        raise ValueError("Video ID must be nonempty.")
    for key in ("duration", "fps"):
        v = video.get(key)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"Video {key} must be finite and positive.")
    duration = float(video["duration"])
    seen: set[str] = set()
    for e in doc.get("events") or []:
        if not e.get("id") or e["id"] in seen:
            raise ValueError("Event IDs must be unique and nonempty.")
        seen.add(e["id"])
        if e.get("type") not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {e.get('type')!r}")
        if not str(e.get("prompt") or ""):
            raise ValueError("Event prompts must be nonempty.")
        for key, lo, hi in (("confidence", 0.0, 1.0), ("volume", 0.0, 1.0), ("pan", -1.0, 1.0)):
            v = e.get(key)
            if not isinstance(v, (int, float)) or not math.isfinite(v) or not (lo <= v <= hi):
                raise ValueError(f"Event {key} must be between {lo} and {hi}.")
        t, d = e.get("t"), e.get("duration")
        if not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0:
            raise ValueError("Event timestamps must be finite and nonnegative.")
        if not isinstance(d, (int, float)) or not math.isfinite(d) or d <= 0:
            raise ValueError("Event durations must be finite and positive.")
        if t + d > duration + 1e-6:
            raise ValueError("Events must start and finish within the video duration.")
    amb = doc.get("ambience")
    if amb is not None:
        if not str(amb.get("prompt") or ""):
            raise ValueError("Ambience prompt must be nonempty.")
        v = amb.get("volume")
        if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            raise ValueError("Ambience volume must be between 0 and 1.")


def postprocess(events: list[Event], duration: float) -> list[Event]:
    """Merge <80 ms neighbours, clamp at clip end, renumber, sort by t."""
    cleaned: list[Event] = []
    for e in events:
        e.type = map_type(e.type)
        e.t = max(0.0, min(float(e.t), max(0.0, duration - 0.05)))
        e.duration = max(0.05, min(float(e.duration), duration - e.t))
        e.volume = max(0.0, min(1.0, float(e.volume)))
        e.pan = max(-1.0, min(1.0, float(e.pan)))
        e.confidence = max(0.0, min(1.0, float(e.confidence)))
        if not e.prompt:
            e.prompt = f"{e.type} sound"
        cleaned.append(e)
    cleaned.sort(key=lambda e: e.t)
    merged: list[Event] = []
    for e in cleaned:
        if merged and (e.t - merged[-1].t) < 0.08:
            if e.confidence > merged[-1].confidence:
                merged[-1] = e
            continue
        merged.append(e)
    for i, e in enumerate(merged, 1):
        e.id = f"e{i}"
    return merged
