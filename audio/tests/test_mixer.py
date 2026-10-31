import numpy as np

from musefx_audio.mixer import (
    OUT_SR, Placement, ambience_bed, apply_fades, limit, mix, pan_gains, prepare_sound, trim_leading_silence,
)

SR = 16000
CEILING = 10 ** (-1 / 20)


def sound_with_silence(lead_seconds=0.05, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * 0.5)) / SR
    body = rng.standard_normal(t.size) * np.exp(-t / 0.08)
    return np.concatenate([np.zeros(int(SR * lead_seconds)), 0.6 * body / np.abs(body).max()]).astype(np.float32)


def onset(channel: np.ndarray, fraction=0.01) -> int:
    return int(np.argmax(np.abs(channel) >= fraction * np.abs(channel).max()))


def test_trim_removes_leading_silence():
    audio = sound_with_silence(0.05)
    assert len(audio) - len(trim_leading_silence(audio)) == int(SR * 0.05)


def test_attack_lands_on_t():
    t = 1.234
    sound = prepare_sound(sound_with_silence(0.07), SR, duration=0.4)
    out = mix([Placement(sound, t, 1.0, 0.0)], n_samples=OUT_SR * 3)
    start = onset(out[:, 0])
    assert t * OUT_SR <= start <= (t + 0.002) * OUT_SR  # within 2 ms, never early


def test_sound_is_cut_to_its_duration():
    sound = prepare_sound(sound_with_silence(), SR, duration=0.2)
    assert len(sound) == int(0.2 * OUT_SR)


def test_fades_start_and_end_at_zero_without_clicks():
    faded = apply_fades(np.ones(OUT_SR // 2, dtype=np.float32), OUT_SR)
    assert faded[0] == 0 and faded[-1] == 0
    assert np.abs(np.diff(faded)).max() < 0.01  # no jump bigger than 1% of full scale


def test_mix_never_passes_minus_1_dbfs():
    loud = [Placement(np.ones(OUT_SR // 10, dtype=np.float32), 0.5, 1.0, 0.0) for _ in range(20)]
    out = mix(loud, n_samples=OUT_SR)
    assert np.abs(out).max() <= CEILING + 1e-6


def test_limiter_leaves_quiet_audio_untouched():
    quiet = (0.3 * np.random.default_rng(0).standard_normal((OUT_SR, 2))).clip(-0.5, 0.5).astype(np.float32)
    assert np.array_equal(limit(quiet, OUT_SR), quiet)


def test_pan_extremes_and_centre():
    sound = prepare_sound(sound_with_silence(), SR, duration=0.3)
    left = mix([Placement(sound, 0.1, 1.0, -1.0)], OUT_SR)
    right = mix([Placement(sound, 0.1, 1.0, 1.0)], OUT_SR)
    assert np.abs(left[:, 1]).max() < 1e-6 and np.abs(right[:, 0]).max() < 1e-6
    l, r = pan_gains(0.0)
    assert np.isclose(l, r) and np.isclose(l**2 + r**2, 1.0)


def test_repeated_sounds_vary():
    base = sound_with_silence()
    rng = np.random.default_rng(0)
    a = prepare_sound(base, SR, 0.4, rng)
    b = prepare_sound(base, SR, 0.4, rng)
    assert not np.array_equal(a, b)


def test_ambience_bed_fills_the_clip_and_fades_at_the_edges():
    n = OUT_SR * 12  # longer than the 10 s source, so it must loop
    source = (0.2 * np.random.default_rng(0).standard_normal(SR * 10)).astype(np.float32)
    bed = ambience_bed(source, SR, n, volume=0.3)
    assert bed.shape == (n, 2)
    assert bed[0].tolist() == [0, 0] and bed[-1].tolist() == [0, 0]
    assert np.abs(bed).max() < 0.3
