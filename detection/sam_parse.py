"""Parse SAM 3.1 streamed output_text into per-frame box tracks.

Wire format (https://dev.meta.ai/docs/sam/reading-segmentation): one line
per frame, `<Nf>` then comma-separated records `id<|box;...|><|mask;...|>`.
Masks are carried inline; Detection needs boxes (position/motion), so mask
payloads are skipped, not decoded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FRAME_RE = re.compile(r"<(\d+)f>(.*)")
BOX_RE = re.compile(
    r"(?P<oid>\d+)<\|box;x1=(?P<x1>\d+);y1=(?P<y1>\d+);x2=(?P<x2>\d+);y2=(?P<y2>\d+)"
    r";w=(?P<w>\d+);h=(?P<h>\d+)(?:;c=(?P<c>[\d.]+))?\|>"
)


@dataclass
class BoxPoint:
    frame: int
    object_id: int
    x1: int
    y1: int
    x2: int
    y2: int
    w: int
    h: int
    confidence: float | None = None

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def pan(self) -> float:
        return max(-1.0, min(1.0, 2.0 * (self.cx / max(1, self.w)) - 1.0))


def parse_output(text: str) -> dict[str, list[BoxPoint]]:
    """Return {object_id: [BoxPoint...]} sorted by frame. Zero matches -> {}."""
    tracks: dict[str, list[BoxPoint]] = {}
    for line in text.splitlines():
        m = FRAME_RE.search(line.strip())
        if not m:
            continue
        frame = int(m.group(1))
        for b in BOX_RE.finditer(m.group(2)):
            p = BoxPoint(
                frame=frame,
                object_id=int(b.group("oid")),
                x1=int(b.group("x1")), y1=int(b.group("y1")),
                x2=int(b.group("x2")), y2=int(b.group("y2")),
                w=int(b.group("w")), h=int(b.group("h")),
                confidence=float(b.group("c")) if b.group("c") else None,
            )
            tracks.setdefault(str(p.object_id), []).append(p)
    for pts in tracks.values():
        pts.sort(key=lambda p: p.frame)
    return tracks


def motion_peaks(points: list[BoxPoint], fps: float) -> list[tuple[float, float]]:
    """(time, strength) for sharp centre-velocity changes (impacts/stops).

    Strength is the frame-to-frame speed drop/rise magnitude, normalised
    roughly to pixels/second. Used to snap rough event times to real motion.
    """
    if len(points) < 3 or fps <= 0:
        return []
    speeds: list[float] = []
    for a, b in zip(points, points[1:]):
        df = max(1, b.frame - a.frame)
        speeds.append((((b.cx - a.cx) ** 2 + (b.cy - a.cy) ** 2) ** 0.5) / df * fps)
    peaks: list[tuple[float, float]] = []
    for i in range(1, len(speeds)):
        change = abs(speeds[i] - speeds[i - 1])
        if change > 0:
            peaks.append((points[i].frame / fps, change))
    peaks.sort(key=lambda p: -p[1])
    return peaks
