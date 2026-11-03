"""Evaluate a generated sound from the audio itself, and say what to fix.

This is the sound-level second pass. Muse Spark cannot hear sound effects
(spikes: WAV upload, sound-in-video, and muse-spark-1.2 input_audio all
returned "no audio"), so semantic listening is out; these measurements are
exact instead. Each candidate is measured, issues are named, the best
candidate wins, and if every candidate has a severe issue the prompt is
revised once with modifiers aimed at that issue and regenerated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .mixer import onset_offset_ms
from .pick import score as pick_score

SEVERE = {"near_silent", "very_soft_attack", "far_too_long"}


@dataclass
class Features:
    onset_ms: float
    effective_duration_s: float
    peak_db: float
    rms_db: float
    centroid_hz: float
    high_freq_ratio: float
    decay_ms: float


def analyze(audio: np.ndarray, sample_rate: int) -> Features:
    if audio.size == 0 or sample_rate <= 0:
        return Features(0.0, 0.0, -120.0, -120.0, 0.0, 0.0, 0.0)
    peak = float(np.abs(audio).max())
    peak_db = 20 * np.log10(peak + 1e-12)
    rms_db = 20 * np.log10(float(np.sqrt(np.mean(audio ** 2))) + 1e-12)
    onset = onset_offset_ms(audio, sample_rate)
    # Effective duration: onset to last sample above -40 dB of peak.
    if peak > 0:
        above = np.nonzero(np.abs(audio) >= peak * 0.01)[0]
        end = int(above[-1]) if above.size else 0
        # Decay: time from peak position to -20 dB after it.
        peak_idx = int(np.argmax(np.abs(audio)))
        after = np.abs(audio[peak_idx:])
        below = np.nonzero(after < peak * 0.1)[0]
        decay = (int(below[0]) / sample_rate * 1000.0) if below.size else (len(after) / sample_rate * 1000.0)
    else:
        end, decay = 0, 0.0
    effective = max(0.0, end / sample_rate - onset / 1000.0)
    # Spectral centroid + high-frequency energy ratio (brightness).
    mag = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) if len(audio) > 1 else np.array([0.0])
    freqs = np.fft.rfftfreq(len(audio), 1 / sample_rate) if len(audio) > 1 else np.array([0.0])
    total = mag.sum()
    centroid = float((freqs * mag).sum() / total) if total > 0 else 0.0
    hf = float(mag[freqs >= 4000].sum() / total) if total > 0 else 0.0
    return Features(onset, effective, peak_db, rms_db, centroid, hf, decay)


def diagnose(f: Features, percussive: bool, requested_duration: float) -> list[str]:
    issues: list[str] = []
    if f.peak_db < -40:
        issues.append("near_silent")
    if f.onset_ms > 150:
        issues.append("very_soft_attack")
    elif f.onset_ms > (40 if percussive else 120):
        issues.append("soft_attack")
    if f.effective_duration_s > requested_duration * 1.8 + 0.2:
        issues.append("far_too_long")
    elif f.effective_duration_s > requested_duration * 1.3 + 0.1:
        issues.append("too_long")
    if requested_duration >= 0.2 and f.effective_duration_s < requested_duration * 0.35:
        issues.append("too_short")
    if f.rms_db < -35:
        issues.append("too_quiet")
    if percussive and 0 < f.centroid_hz < 700:
        issues.append("dull_for_percussive")
    if f.high_freq_ratio > 0.75 and f.centroid_hz > 6000:
        issues.append("harsh_bright")
    return issues


def severe_issues(issues: list[str]) -> list[str]:
    return [i for i in issues if i in SEVERE]


_MODIFIERS = {
    "near_silent": "loud, powerful, close-miked",
    "too_quiet": "loud, powerful, close-miked",
    "very_soft_attack": "sharp immediate attack, hard transient, no fade-in",
    "soft_attack": "sharp immediate attack, hard transient, no fade-in",
    "far_too_long": "very short, abruptly ending, no tail",
    "too_long": "short and tight, quick decay",
    "too_short": "fuller, sustained, complete decay",
    "dull_for_percussive": "bright, crisp, detailed texture",
    "harsh_bright": "warm, rounded, softer top end",
}


def revise_prompt(prompt: str, issues: list[str]) -> str:
    mods: list[str] = []
    for issue in issues:
        mod = _MODIFIERS.get(issue)
        if mod and mod not in mods:
            mods.append(mod)
    return prompt if not mods else f"{prompt}, {', '.join(mods)}"


@dataclass
class Picked:
    index: int
    issues: list[str] = field(default_factory=list)
    features: Features | None = None


def best_candidate(candidates: list[np.ndarray], sample_rate: int,
                    percussive: bool, requested_duration: float) -> Picked:
    """Pick by the existing onset/loudness score, penalised per diagnosed issue."""
    best: Picked | None = None
    best_value = -1e9
    for i, cand in enumerate(candidates):
        feats = analyze(cand, sample_rate)
        issues = diagnose(feats, percussive, requested_duration)
        value = pick_score(cand, sample_rate, percussive) - 0.25 * len(issues) - 0.5 * len(severe_issues(issues))
        if value > best_value:
            best_value = value
            best = Picked(i, issues, feats)
    assert best is not None
    return best
