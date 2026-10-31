import re
import subprocess

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


def test_second_run_reuses_cached_sounds(sample_video, sample_events, tmp_path):
    cache = tmp_path / "cache"
    render(sample_video, sample_events, tmp_path / "a.mp4", FakeGenerator(), cache_dir=cache)
    generator = FakeGenerator()
    result = render(sample_video, sample_events, tmp_path / "b.mp4", generator, cache_dir=cache)
    assert generator.calls == 0
    assert result.sounds_generated == 0 and result.sounds_from_cache == 7
