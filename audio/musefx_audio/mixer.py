"""Turn picked sounds into one stereo track, synced to the video.

Per sound: trim leading silence (so the attack lands on t), normalise, vary
repeats, cut to the event duration, fade in 5 ms and out 20-50 ms. Then place
each sound with its volume and pan, add the ambience bed, and limit the mix
to -1 dBFS.
"""

from dataclasses import dataclass
from math import gcd

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import resample, resample_poly

OUT_SR = 48000
CEILING_DB = -1.0
SOUND_PEAK = 10 ** (-3 / 20)  # each sound is normalised here before its event volume
SILENCE_DB = -40.0  # below this, relative to the sound's peak, counts as leading silence
FADE_IN_SECONDS = 0.005
FADE_OUT_MIN, FADE_OUT_MAX = 0.02, 0.05
VARY_SEMITONES = 2.0
VARY_DB = 3.0
AMBIENCE_GAIN = 0.25  # keeps the bed well under the effects
AMBIENCE_EDGE_FADE = 1.0
AMBIENCE_LOOP_CROSSFADE = 0.5
LIMITER_LOOKAHEAD = 0.005


@dataclass(frozen=True)
class Placement:
    audio: np.ndarray  # mono, OUT_SR, already prepared
    t: float
    volume: float
    pan: float


def prepare_sound(
    audio: np.ndarray, sample_rate: int, duration: float, rng: np.random.Generator | None = None
) -> np.ndarray:
    """Resample, trim, normalise, optionally vary, cut to duration and fade one sound."""
    audio = trim_leading_silence(resample_to(audio, sample_rate, OUT_SR))
    audio = normalise_peak(audio, SOUND_PEAK)
    if rng is not None:
        audio = vary(audio, rng)
    audio = audio[: max(1, int(round(duration * OUT_SR)))]
    return apply_fades(audio, OUT_SR)


def resample_to(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return audio.astype(np.float32)
    g = gcd(sr_in, sr_out)
    return resample_poly(audio, sr_out // g, sr_in // g).astype(np.float32)


def trim_leading_silence(audio: np.ndarray, threshold_db: float = SILENCE_DB) -> np.ndarray:
    peak = np.abs(audio).max() if audio.size else 0.0
    if peak == 0:
        return audio
    start = int(np.argmax(np.abs(audio) >= peak * 10 ** (threshold_db / 20)))
    return audio[start:]


def normalise_peak(audio: np.ndarray, target: float) -> np.ndarray:
    peak = np.abs(audio).max() if audio.size else 0.0
    return audio if peak == 0 else (audio * (target / peak)).astype(np.float32)


def vary(audio: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Shift pitch by up to ±2 semitones and volume by up to ±3 dB."""
    factor = 2 ** (rng.uniform(-VARY_SEMITONES, VARY_SEMITONES) / 12)
    shifted = resample(audio, max(1, int(round(len(audio) / factor))))
    gain = 10 ** (rng.uniform(-VARY_DB, VARY_DB) / 20)
    return (shifted * gain).astype(np.float32)


def apply_fades(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """5 ms fade-in from 0, and a 20-50 ms fade-out ending at exactly 0, so edges never click."""
    out = audio.astype(np.float32).copy()
    n = len(out)
    fade_in = min(n, int(FADE_IN_SECONDS * sample_rate))
    fade_out_seconds = float(np.clip(0.1 * n / sample_rate, FADE_OUT_MIN, FADE_OUT_MAX))
    fade_out = min(n, int(fade_out_seconds * sample_rate))
    if fade_in:
        out[:fade_in] *= np.linspace(0, 1, fade_in, endpoint=False)
    if fade_out:
        out[n - fade_out :] *= np.linspace(1, 0, fade_out)
    return out


def pan_gains(pan: float) -> tuple[float, float]:
    """Constant-power pan: -1 is hard left, 0 is centre (-3 dB each side), 1 is hard right."""
    angle = (pan + 1) * np.pi / 4
    return float(np.cos(angle)), float(np.sin(angle))


def ambience_bed(audio: np.ndarray, sample_rate: int, n_samples: int, volume: float) -> np.ndarray:
    """Loop the ambience to the clip length with crossfades; stereo, faded in and out."""
    audio = normalise_peak(resample_to(audio, sample_rate, OUT_SR), SOUND_PEAK)
    if audio.size == 0:
        return np.zeros((n_samples, 2), dtype=np.float32)
    bed = audio
    crossfade = min(int(AMBIENCE_LOOP_CROSSFADE * OUT_SR), len(audio) // 4)
    while len(bed) < n_samples:
        bed = crossfade_concat(bed, audio, crossfade)
    bed = bed[:n_samples].copy()

    edge = min(int(AMBIENCE_EDGE_FADE * OUT_SR), n_samples // 4)
    if edge:
        bed[:edge] *= np.linspace(0, 1, edge)
        bed[n_samples - edge :] *= np.linspace(1, 0, edge)

    left, right = pan_gains(0.0)
    gain = volume * AMBIENCE_GAIN
    return np.stack([bed * left * gain, bed * right * gain], axis=1).astype(np.float32)


def crossfade_concat(a: np.ndarray, b: np.ndarray, overlap: int) -> np.ndarray:
    """Join b onto the end of a with an equal-power crossfade."""
    if overlap <= 0:
        return np.concatenate([a, b])
    ramp = np.linspace(0, np.pi / 2, overlap)
    middle = a[-overlap:] * np.cos(ramp) + b[:overlap] * np.sin(ramp)
    return np.concatenate([a[:-overlap], middle, b[overlap:]]).astype(np.float32)


def mix(placements: list[Placement], n_samples: int, ambience: np.ndarray | None = None) -> np.ndarray:
    """Place every sound at its time, add the ambience, and limit. Returns (n_samples, 2) float32."""
    out = np.zeros((n_samples, 2), dtype=np.float32)
    for p in placements:
        start = int(round(p.t * OUT_SR))
        if start >= n_samples:
            continue
        segment = p.audio[: n_samples - start]
        left, right = pan_gains(p.pan)
        out[start : start + len(segment), 0] += segment * (p.volume * left)
        out[start : start + len(segment), 1] += segment * (p.volume * right)
    if ambience is not None:
        out += ambience[:n_samples]
    return limit(out, OUT_SR)


def limit(audio: np.ndarray, sample_rate: int, ceiling_db: float = CEILING_DB) -> np.ndarray:
    """Look-ahead peak limiter: the gain dips smoothly before a peak, so nothing passes the ceiling."""
    ceiling = 10 ** (ceiling_db / 20)
    peak = np.abs(audio).max(axis=1)
    gain = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    if gain.min() < 1.0:
        w = max(1, int(LIMITER_LOOKAHEAD * sample_rate))
        # Every sample in the averaging window [n - w/2, n + w/2] took the minimum over
        # [m - w, m + w], which covers n, so the smoothed gain never exceeds the gain n needs.
        gain = minimum_filter1d(gain, size=2 * w + 1)
        gain = uniform_filter1d(gain, size=w | 1)
    return np.clip(audio * gain[:, None], -ceiling, ceiling).astype(np.float32)
