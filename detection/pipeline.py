"""Detection pipeline: MP4 -> events.json (see README.md / PLAN.md)."""
from __future__ import annotations

import base64
import json
import pathlib
import subprocess
from typing import Callable

from .events import Event, EventsDocument, map_type, postprocess, validate
from .meta_client import MetaClient
from .sam_parse import motion_peaks, parse_output

ProgressFn = Callable[[str, int], None]


def _run(cmd: list[str]) -> bytes:
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd[:3])}... {r.stderr[:300]!r}")
    return r.stdout


def probe(video: pathlib.Path) -> dict:
    out = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)])
    meta = json.loads(out)
    stream = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), None)
    if stream is None:
        raise ValueError("The uploaded file does not contain a video stream.")
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if not (width > 0 and height > 0 and max(width, height) <= 1920 and min(width, height) <= 1080):
        raise ValueError("Please upload a video up to 1080p (1920 by 1080, or portrait equivalent).")
    duration = float((meta.get("format") or {}).get("duration") or 0)
    if not (0 < duration <= 60.0):
        raise ValueError("Please upload a video of 60 seconds or less.")
    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key) or ""
        if "/" in raw:
            n, d = raw.split("/", 1)
            try:
                fps = float(n) / float(d)
            except (ValueError, ZeroDivisionError):
                pass
        if fps > 0:
            break
    if fps <= 0:
        raise ValueError("Video frame rate is unavailable.")
    return {"duration": duration, "fps": fps, "width": width, "height": height}


def _extract_frame(video: pathlib.Path, t: float, out: pathlib.Path) -> str | None:
    try:
        _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
              "-frames:v", "1", "-q:v", "3", str(out)])
        return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode()
    except Exception:
        return None


SCENE_PROMPT = (
    "You are the scene stage of a video-to-sound-effects pipeline. "
    "Return ONLY JSON: {\"setting\": string, \"surface\": string, \"objects\": [short noun phrases to track, max 4], "
    "\"candidate_events\": [{\"rough_t\": number (seconds), \"type\": one of footstep|impact|hit|door|pickup|ui|whoosh|other, "
    "\"description\": string}], \"ambience_prompt\": string or null}. "
    "objects must name every distinct visible moving object or shape as a short noun phrase a segmentation model can find, "
    "e.g. \"player character\", \"red rectangle\", \"soccer ball\" — include abstract shapes, not only game characters. "
    "candidate_events includes appearances, stops, direction changes, and contacts that plausibly make a sound. "
    "If truly nothing moves or appears, return empty arrays."
)


def _concept_variants(concept: str) -> list[str]:
    """SAM prompt retries: original, shape swap, noun only (deduped, ordered)."""
    c = " ".join((concept or "").strip().lower().split())
    if not c:
        return []
    out = [c]
    for a, b in (("rectangle", "square"), ("square", "rectangle"), ("circle", "ball"), ("ball", "circle")):
        if a in c:
            out.append(c.replace(a, b))
    words = c.split()
    if len(words) > 1:
        out.append(words[-1])
        for a, b in (("rectangle", "square"), ("square", "rectangle")):
            if words[-1] == a:
                out.append(b)
    seen: set[str] = set()
    return [v for v in out if not (v in seen or seen.add(v))]


def _parse_json(text: str) -> dict:
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model did not return JSON")
    return json.loads(text[start:end + 1])


def detect(video: pathlib.Path, video_id: str, client: MetaClient | None = None,
           progress: ProgressFn | None = None, workdir: pathlib.Path | None = None) -> dict:
    def report(stage: str, pct: int) -> None:
        if progress:
            progress(stage, pct)

    meta = probe(video)
    duration, fps = meta["duration"], meta["fps"]
    client = client or MetaClient()
    workdir = workdir or video.parent
    workdir.mkdir(parents=True, exist_ok=True)

    report("detecting", 15)
    file_id = client.upload_file(video)
    scene = _parse_json(client.muse(SCENE_PROMPT, file_id=file_id))
    objects = [str(o).strip() for o in (scene.get("objects") or [])][:4]
    candidates = scene.get("candidate_events") or []

    # SAM tracking per concept (one request per concept, per docs).
    # SAM concept matching is literal: retry with shape/noun variants on zero matches.
    report("detecting", 30)
    tracks: list = []
    all_points: list = []
    for concept in objects:
        for variant in _concept_variants(concept):
            try:
                raw = client.sam_track_text(variant, file_id)
            except Exception:
                continue
            parsed = parse_output(raw)
            if parsed:
                for pts in parsed.values():
                    tracks.append((concept, pts))
                    all_points.extend(pts)
                break

    # Snap candidate times to nearby motion peaks; fall back to Muse times.
    peaks: list[tuple[float, float]] = []
    for _, pts in tracks:
        peaks.extend(motion_peaks(pts, fps))
    peaks.sort(key=lambda p: -p[1])

    def snap(t: float) -> tuple[float, bool]:
        near = [(abs(p[0] - t), p[0]) for p in peaks if abs(p[0] - t) <= 0.5]
        return (min(near)[1], True) if near else (t, False)

    def pan_at(t: float) -> float:
        if not all_points:
            return 0.0
        nearest = min(all_points, key=lambda p: abs(p.frame / fps - t))
        return nearest.pan

    report("describing", 45)
    events: list[Event] = []
    for i, c in enumerate(candidates):
        try:
            rough = float(c.get("rough_t", 0.0))
        except (TypeError, ValueError):
            continue
        t, snapped = snap(rough)
        etype = map_type(str(c.get("type", "other")))
        desc = str(c.get("description") or etype)
        # Prompt pass: 3 frames around the event (best-effort).
        prompt_text, dur, vol = f"{desc} sound effect", 0.4, 0.8
        frames = [u for u in (_extract_frame(video, t + d, workdir / f"ev{i}_{j}.jpg")
                              for j, d in enumerate((-0.2, 0.0, 0.2))) if u]
        if frames:
            try:
                out = client.muse(
                    "Return ONLY JSON {\"prompt\": string (AudioGen sound prompt), \"duration\": number, \"volume\": number 0-1} "
                    f"for this sound event in a {scene.get('setting', 'game')} scene, surface {scene.get('surface', 'unknown')}: {desc} ({etype}). "
                    "The prompt describes only the sound, e.g. 'heavy boot on gravel, outdoors'.",
                    image_data_urls=frames)
                parsed = _parse_json(out)
                prompt_text = str(parsed.get("prompt") or prompt_text)
                dur = float(parsed.get("duration") or dur)
                vol = float(parsed.get("volume") or vol)
            except Exception:
                pass
        events.append(Event(id=f"tmp{i}", t=t, duration=dur, type=etype, prompt=prompt_text,
                            volume=vol, pan=pan_at(t), confidence=0.75 if snapped else 0.55))

    if not events and peaks:
        report("describing", 45)
        for t, _strength in peaks[:12]:
            events.append(Event(id="tmp", t=t, duration=0.35, type="other",
                                prompt="impact sound effect", volume=0.7, pan=pan_at(t), confidence=0.4))

    ambience = None
    if scene.get("ambience_prompt"):
        ambience = {"prompt": str(scene["ambience_prompt"]), "volume": 0.3}

    doc = EventsDocument(video_id=video_id, duration=duration, fps=fps,
                         events=postprocess(events, duration), ambience=ambience)
    result = doc.to_dict()
    validate(result)
    report("describing", 55)
    return result
