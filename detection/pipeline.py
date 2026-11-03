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
    "Each candidate description must be detailed: name the object's appearance (colour, size, material), the exact action, and what it contacts. "
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


def _slot(slots: dict, key: str) -> str:
    return " ".join(str(slots.get(key) or "").strip().strip(",.;").split())


def _duration_character(duration: float) -> str:
    if duration <= 0.25:
        return "very short, immediate snap"
    if duration <= 0.6:
        return "short, punchy"
    if duration <= 1.5:
        return "medium length, natural decay"
    return "longer, fully decaying"


def compose_prompt(slots: dict, etype: str, fallback: str, duration: float = 0.4) -> str:
    """Compose a detailed AudioGen prompt from structured slots.

    Slots: subject, subject_detail, action, contact, material, secondary,
    motion, environment, acoustics, intensity, perspective, character.
    Empty slots are skipped; with no subject/action the fallback is used.
    The result names the sound source, what it does, what it contacts,
    how hard, the space it rings in, and how it should be recorded —
    material detail is what separates a usable prompt from a generic one.
    """
    subject = _slot(slots, "subject")
    action = _slot(slots, "action")
    if not (subject or action):
        return fallback
    detail = _slot(slots, "subject_detail")
    intensity = _slot(slots, "intensity")
    contact = _slot(slots, "contact")
    material = _slot(slots, "material")
    secondary = _slot(slots, "secondary")
    motion = _slot(slots, "motion")
    environment = _slot(slots, "environment")
    acoustics = _slot(slots, "acoustics")
    perspective = _slot(slots, "perspective")
    character = _slot(slots, "character")

    subject_phrase = f"{detail} {subject}".strip() if detail and detail.lower() not in subject.lower() else subject
    head = " ".join(p for p in (intensity, subject_phrase, action) if p)
    if contact:
        head += f", {contact}"
    elif material:
        head += f" on {material}"
    if material and contact and material.lower() not in contact.lower():
        head += f" on {material}"
    if motion and motion.lower() not in head.lower():
        head += f", {motion}"
    parts = [head]
    if secondary:
        parts.append(secondary)
    space = ", ".join(p for p in (environment, acoustics) if p)
    if space:
        parts.append(space)
    if perspective:
        parts.append(perspective)
    parts.append(character or f"close and dry, {_duration_character(duration)}, single {etype}")
    parts.append("isolated sound effect only, no music, no speech")
    prompt = ", ".join(p for p in parts if p)
    return prompt[:400].rstrip(",.; ") if len(prompt) > 400 else prompt


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

    def track_detail(t: float) -> dict:
        """Objective motion/scale detail from SAM boxes near t, for prompt slots."""
        if not all_points or fps <= 0:
            return {}
        pts = sorted(all_points, key=lambda p: p.frame)
        near = min(pts, key=lambda p: abs(p.frame / fps - t))
        before = min(pts, key=lambda p: abs(p.frame / fps - (t - 0.25)))
        after = min(pts, key=lambda p: abs(p.frame / fps - (t + 0.25)))
        dx = (after.cx - before.cx) / max(1, near.w)
        dy = (after.cy - before.cy) / max(1, near.h)
        speed = (dx * dx + dy * dy) ** 0.5 * 2  # frame-widths per second, roughly
        words: list[str] = []
        if abs(dx) > 0.08:
            direction = "left to right" if dx > 0 else "right to left"
            words.append(("moving rapidly " if speed > 0.6 else "moving ") + direction)
        if abs(dy) > 0.08:
            words.append("dropping downward" if dy > 0 else "rising upward")
        if not words and len(pts) > 2:
            words.append("suddenly stopping" if snapped_hint(t, peaks) else "briefly visible")
        area = max(0.0, (near.x2 - near.x1) * (near.y2 - near.y1)) / max(1, near.w * near.h)
        scale = "large and close in frame" if area > 0.2 else "medium sized in frame" if area > 0.05 else "small in frame"
        return {"motion": ", ".join(words), "subject_detail_extra": scale}

    def snapped_hint(t: float, pk: list) -> bool:
        return any(abs(p[0] - t) <= 0.1 for p in pk)

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
                detail = track_detail(t)
                out = client.muse(
                    "Return ONLY JSON {\"slots\": {\"subject\": string, \"subject_detail\": string, \"action\": string, "
                    "\"contact\": string, \"material\": string, \"secondary\": string, \"environment\": string, "
                    "\"acoustics\": string, \"intensity\": string, \"perspective\": string, \"character\": string}, "
                    "\"duration\": number, \"volume\": number 0-1} "
                    f"for this sound event in a {scene.get('setting', 'game')} scene, surface {scene.get('surface', 'unknown')}: {desc} ({etype}). "
                    f"Tracked motion near the event: {detail.get('motion', 'still')}; object scale: {detail.get('subject_detail_extra', 'unknown')}. "
                    "Fill EVERY slot concretely from the frames: subject = exact object; subject_detail = its colour, size, shape and what it is made of; "
                    "action = the precise movement or contact at this instant; contact = how the surfaces meet (e.g. 'sole compressing and scraping'); "
                    "material = the surface or object contacted and its material; secondary = any smaller accompanying texture (e.g. 'loose stones crunching, fabric rustle'); "
                    "environment = place; acoustics = the space's echo character (e.g. 'tight indoor room, minimal reverb'); "
                    "intensity = force and speed (e.g. 'heavy and forceful'); perspective = microphone distance/position (e.g. 'very close, slightly left'); "
                    "character = onset and decay (e.g. 'sharp attack, short ringing decay'). "
                    "Never use generic words like 'sound effect', 'noise', or 'various sounds'.",
                    image_data_urls=frames)
                parsed = _parse_json(out)
                dur = float(parsed.get("duration") or dur)
                vol = float(parsed.get("volume") or vol)
                slots = dict(parsed.get("slots") or {})
                if detail.get("motion"):
                    slots.setdefault("motion", detail["motion"])
                if detail.get("subject_detail_extra") and not slots.get("subject_detail"):
                    slots["subject_detail"] = detail["subject_detail_extra"]
                prompt_text = compose_prompt(slots, etype, prompt_text, duration=dur)
            except Exception:
                pass
        events.append(Event(id=f"tmp{i}", t=t, duration=dur, type=etype, prompt=prompt_text,
                            volume=vol, pan=pan_at(t), confidence=0.75 if snapped else 0.55))

    if not events and peaks:
        report("describing", 45)
        concept = tracks[0][0] if tracks else "object"
        for t, _strength in peaks[:12]:
            detail = track_detail(t)
            fallback_prompt = compose_prompt(
                {"subject": concept, "action": "striking or stopping abruptly",
                 "motion": detail.get("motion", ""), "environment": str(scene.get("setting") or "")},
                "other", f"{concept} impact, close and dry, single hit", duration=0.35)
            events.append(Event(id="tmp", t=t, duration=0.35, type="other",
                                prompt=fallback_prompt, volume=0.7, pan=pan_at(t), confidence=0.4))

    ambience = None
    if scene.get("ambience_prompt"):
        ambience = {"prompt": str(scene["ambience_prompt"]), "volume": 0.3}

    doc = EventsDocument(video_id=video_id, duration=duration, fps=fps,
                         events=postprocess(events, duration), ambience=ambience)
    result = doc.to_dict()
    validate(result)
    report("describing", 55)
    return result


REVIEW_PROMPT = (
    "You are reviewing a video that has generated sound effects mixed in. Listen and watch. "
    "For EACH listed event, judge: does a sound start exactly when the action happens, and does the sound match the action? "
    "Return ONLY JSON {\"verdicts\": [{\"id\": string, \"timing_shift_ms\": number (milliseconds to ADD to the event time; "
    "positive if the sound starts too early, negative if too late, 0 if on time), \"match\": number 0-1 (1 = perfect sound for the action), "
    "\"action\": \"keep\"|\"shift\"|\"regenerate\", \"slots\": {\"subject\": string, \"subject_detail\": string, \"action\": string, "
    "\"contact\": string, \"material\": string, \"secondary\": string, \"environment\": string, \"acoustics\": string, "
    "\"intensity\": string, \"perspective\": string, \"character\": string} or null}]}. "
    "Use \"regenerate\" only when the sound is wrong for the action (match < 0.6) and fill slots with a much more specific description. "
    "Use \"shift\" when the sound is right but early/late by 30 ms or more. Otherwise \"keep\". Events: "
)


def review_result(video: pathlib.Path, events: dict, client: MetaClient | None = None) -> list[dict]:
    """Muse Spark second pass over the finished video+audio. Returns verdict dicts."""
    client = client or MetaClient()
    listing = json.dumps([
        {"id": e["id"], "t": e["t"], "type": e["type"], "prompt": e["prompt"]}
        for e in events.get("events", [])
    ])
    file_id = client.upload_file(video)
    parsed = _parse_json(client.muse(REVIEW_PROMPT + listing, file_id=file_id))
    verdicts: list[dict] = []
    for v in parsed.get("verdicts", []) or []:
        if not isinstance(v, dict) or not v.get("id"):
            continue
        verdicts.append({
            "id": str(v["id"]),
            "timing_shift_ms": float(v.get("timing_shift_ms") or 0.0),
            "match": max(0.0, min(1.0, float(v.get("match") if v.get("match") is not None else 1.0))),
            "action": v.get("action") if v.get("action") in ("keep", "shift", "regenerate") else "keep",
            "slots": v.get("slots") if isinstance(v.get("slots"), dict) else None,
        })
    return verdicts
