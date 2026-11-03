"""Component 2 end to end: (video, events.json) -> video with synced sound effects."""

import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .cache import SoundCache
from .diagnose import best_candidate, revise_prompt, severe_issues
from .events import load_events
from .generators import Generator
from .merge import merge_audio, probe_duration
from .mixer import OUT_SR, Placement, ambience_bed, mix, prepare_sound
from .pick import PERCUSSIVE_TYPES

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "sounds"
AMBIENCE_SECONDS = 10.0  # AudioGen's comfortable maximum; the bed is looped to the clip length

# on_progress(stage, fraction): stage is "generating" or "mixing" (Backend API stage names).
ProgressFn = Callable[[str, float], None]


@dataclass(frozen=True)
class RenderResult:
    out_path: Path
    events_played: int
    events_skipped: int
    sounds_generated: int
    sounds_from_cache: int


def render(
    video_path: str | Path,
    events_path: str | Path,
    out_path: str | Path,
    generator: Generator,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    n_candidates: int = 3,
    seed: int = 0,
    on_progress: ProgressFn | None = None,
) -> RenderResult:
    progress = on_progress or (lambda stage, fraction: None)
    events_file = load_events(events_path)
    video_duration = probe_duration(video_path)  # the real length wins over events.json
    events = [e for e in events_file.audible_events() if e.t < video_duration]
    ambience = events_file.ambience
    cache = SoundCache(cache_dir, generator.name)

    # One sound per distinct (prompt, duration); repeats reuse it.
    wanted: dict[str, tuple[str, float, bool]] = {}
    for e in events:
        wanted.setdefault(cache.key(e.prompt, e.duration), (e.prompt, e.duration, e.type in PERCUSSIVE_TYPES))
    ambience_key = cache.key(ambience.prompt, AMBIENCE_SECONDS) if ambience else None
    if ambience:
        wanted.setdefault(ambience_key, (ambience.prompt, AMBIENCE_SECONDS, False))

    progress("generating", 0.0)
    sounds: dict[str, tuple[np.ndarray, int]] = {}
    for key, (prompt, duration, _) in wanted.items():
        hit = cache.get(prompt, duration)
        if hit is not None:
            sounds[key] = hit
    from_cache = len(sounds)
    missing = [key for key in wanted if key not in sounds]

    # Ambience goes in its own request so short effects aren't padded to its length.
    groups = [[k for k in missing if k != ambience_key], [k for k in missing if k == ambience_key]]
    for group in groups:
        if not group:
            continue
        prompts = [wanted[k][0] for k in group]
        durations = [wanted[k][1] for k in group]
        sample_rate, candidates = generator.generate(prompts, durations, n_candidates, seed)
        for key, options in zip(group, candidates):
            prompt, duration, percussive = wanted[key]
            picked = best_candidate(options, sample_rate, percussive, duration)
            best = options[picked.index]
            # Sound-level second pass: if every candidate has a severe issue
            # (silent, very soft attack, far too long), revise the prompt once
            # aimed at the issue and regenerate; keep whichever set is healthier.
            severe = severe_issues(picked.issues)
            if severe and options:
                revised = revise_prompt(prompt, severe)
                try:
                    sr2, regen = generator.generate([revised], [duration], n_candidates, seed + 997)
                    if regen and regen[0]:
                        picked2 = best_candidate(regen[0], sr2, percussive, duration)
                        if len(severe_issues(picked2.issues)) < len(severe):
                            best, sample_rate = regen[0][picked2.index], sr2
                except Exception:
                    pass  # diagnosis must never break generation
            cache.put(prompt, duration, best, sample_rate)
            sounds[key] = (best, sample_rate)
    progress("generating", 1.0)

    progress("mixing", 0.0)
    uses = Counter(cache.key(e.prompt, e.duration) for e in events)
    rng = np.random.default_rng(seed)
    placements = []
    for e in events:
        key = cache.key(e.prompt, e.duration)
        audio, sample_rate = sounds[key]
        # Only sounds used more than once get pitch/volume variation.
        prepared = prepare_sound(audio, sample_rate, e.duration, rng if uses[key] > 1 else None)
        placements.append(Placement(prepared, e.t, e.volume, e.pan))

    n_samples = int(round(video_duration * OUT_SR))
    bed = None
    if ambience:
        audio, sample_rate = sounds[ambience_key]
        bed = ambience_bed(audio, sample_rate, n_samples, ambience.volume)
    track = mix(placements, n_samples, bed)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "mix.wav"
        sf.write(wav, track, OUT_SR, subtype="PCM_24")
        merge_audio(video_path, wav, out_path)
    progress("mixing", 1.0)

    return RenderResult(
        out_path=out_path,
        events_played=len(events),
        events_skipped=len(events_file.events) - len(events),
        sounds_generated=len(missing),
        sounds_from_cache=from_cache,
    )
