import numpy as np

from musefx_audio.pick import pick_best

SR = 16000


def burst(attack_seconds: float) -> np.ndarray:
    t = np.arange(int(SR * 0.4)) / SR
    rng = np.random.default_rng(1)
    sound = rng.standard_normal(t.size) * np.exp(-t / 0.1)
    if attack_seconds:
        sound *= np.clip(t / attack_seconds, 0, 1)
    return (0.5 * sound / np.abs(sound).max()).astype(np.float32)


def test_percussive_prefers_the_sharp_start():
    assert pick_best([burst(0.06), burst(0.0), burst(0.03)], SR, percussive=True) == 1


def test_silent_candidate_never_wins():
    silent = np.zeros(SR, dtype=np.float32)
    assert pick_best([silent, burst(0.06)], SR, percussive=True) == 1
    assert pick_best([silent, burst(0.06)], SR, percussive=False) == 1


def test_non_percussive_prefers_a_steady_level():
    t = np.arange(SR) / SR
    steady = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    spiky = steady * (np.sin(2 * np.pi * 3 * t) > 0.9)
    assert pick_best([spiky.astype(np.float32), steady], SR, percussive=False) == 1
