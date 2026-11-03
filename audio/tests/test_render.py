import re
import subprocess

import numpy as np
import pytest
import soundfile as sf

from musefx_audio.generators import FakeGenerator
from musefx_audio.merge import ffmpeg_exe, probe_duration
from musefx_audio.render import render


def stream_md5(path, stream: str) -> str:
    out = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", stream, "-c", "copy", "-f", "md5", "-"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_end_to_end_with_fake_sounds(sample_video, sample_events, tmp_path):
    out = tmp_path / "out.mp4"
    stages = []
    generator = FakeGenerator()
    result = render(
        sample_video, sample_events, out, generator, cache_dir=tmp_path / "cache",
        on_progress=lambda stage, fraction: stages.append((stage, fraction)),
    )

    assert out.exists()
    assert result.events_played == 9 and result.events_skipped == 1
    assert result.sounds_generated == 7  # 6 distinct effect prompts + ambience
    assert generator.calls == 2  # effects and ambience are separate requests
    assert stages[0] == ("generating", 0.0) and stages[-1] == ("mixing", 1.0)

    info = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
    assert re.search(r"Audio: aac", info)
    assert stream_md5(out, "0:v:0") == stream_md5(sample_video, "0:v:0")  # video copied, not re-encoded
    assert abs(probe_duration(out) - probe_duration(sample_video)) < 0.1


def test_audio_already_in_the_clip_is_kept(sample_video, sample_events, tmp_path):
    with_audio = tmp_path / "with_audio.mp4"
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(sample_video), "-f", "lavfi", "-i", "sine=frequency=3000:duration=8",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", str(with_audio),
        ],
        check=True,
    )
    out = tmp_path / "out.mp4"
    render(with_audio, sample_events, out, FakeGenerator(), cache_dir=tmp_path / "cache")

    wav = tmp_path / "out.wav"
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(out), str(wav)], check=True)
    audio, sr = sf.read(wav)
    assert audio.shape[1] == 2  # a mono source must not collapse the panned mix
    spectrum = np.abs(np.fft.rfft(audio.mean(axis=1)))
    freqs = np.fft.rfftfreq(len(audio), 1 / sr)
    assert freqs[np.argmax(np.where(freqs > 2900, spectrum, 0))] == pytest.approx(3000, abs=5)
    assert stream_md5(out, "0:v:0") == stream_md5(with_audio, "0:v:0")


def test_fake_sounds_are_never_reused_by_a_real_generator(sample_video, sample_events, tmp_path):
    cache = tmp_path / "cache"
    render(sample_video, sample_events, tmp_path / "a.mp4", FakeGenerator(), cache_dir=cache)

    class RealGenerator(FakeGenerator):
        name = "audiogen"

    generator = RealGenerator()
    result = render(sample_video, sample_events, tmp_path / "b.mp4", generator, cache_dir=cache)
    assert generator.calls == 2
    assert result.sounds_generated == 7 and result.sounds_from_cache == 0


def test_second_run_reuses_cached_sounds(sample_video, sample_events, tmp_path):
    cache = tmp_path / "cache"
    render(sample_video, sample_events, tmp_path / "a.mp4", FakeGenerator(), cache_dir=cache)
    generator = FakeGenerator()
    result = render(sample_video, sample_events, tmp_path / "b.mp4", generator, cache_dir=cache)
    assert generator.calls == 0
    assert result.sounds_generated == 0 and result.sounds_from_cache == 7
