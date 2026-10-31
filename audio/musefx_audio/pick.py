"""Pick the best of several generated options for one sound.

Percussive sounds (footsteps, impacts...) score on a sharp start plus healthy
loudness; the rest (whoosh, other, ambience) score on loudness plus a steady
level. A prompt-match score (audio-text similarity) can be added later.
"""

import numpy as np

PERCUSSIVE_TYPES = {"footstep", "impact", "hit", "door", "pickup", "ui"}

_FRAME_SECONDS = 0.002


def pick_best(candidates: list[np.ndarray], sample_rate: int, percussive: bool) -> int:
    """Index of the best candidate."""
    scores = [score(c, sample_rate, percussive) for c in candidates]
    return int(np.argmax(scores))


def score(audio: np.ndarray, sample_rate: int, percussive: bool) -> float:
    env = envelope(audio, sample_rate)
    peak = env.max() if env.size else 0.0
    if peak < 1e-4:
        return -1.0  # silent or near-silent

    rms_db = 20 * np.log10(np.sqrt(np.mean(audio**2)) + 1e-9)
    loudness = float(np.clip((rms_db + 45) / 30, 0, 1))  # -45 dBFS -> 0, -15 dBFS -> 1
    clipped = np.mean(np.abs(audio) >= 0.999)
    loudness -= min(1.0, clipped * 100)

    if percussive:
        return 0.6 * attack_sharpness(env) + 0.4 * loudness
    return 0.5 * loudness + 0.5 * steadiness(env)


def envelope(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Peak level per 2 ms frame."""
    frame = max(1, int(sample_rate * _FRAME_SECONDS))
    n = len(audio) // frame
    if n == 0:
        return np.abs(audio)
    return np.abs(audio[: n * frame]).reshape(n, frame).max(axis=1)


def attack_sharpness(env: np.ndarray) -> float:
    """1.0 when the sound rises from 10% to 90% of its peak instantly, falling off over ~20 ms."""
    peak = env.max()
    start = int(np.argmax(env >= 0.1 * peak))
    top = start + int(np.argmax(env[start:] >= 0.9 * peak))
    attack_ms = (top - start) * _FRAME_SECONDS * 1000
    return 1.0 / (1.0 + attack_ms / 10.0)


def steadiness(env: np.ndarray) -> float:
    """1.0 for a level that barely moves; 0 when it swings by 20 dB or more."""
    active = env[env > 0.05 * env.max()]
    if active.size < 2:
        return 0.0
    spread_db = np.std(20 * np.log10(active + 1e-9))
    return float(np.clip(1 - spread_db / 20, 0, 1))
