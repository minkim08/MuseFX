import subprocess
from pathlib import Path

import pytest

from musefx_audio.merge import ffmpeg_exe

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="session")
def sample_events() -> Path:
    return FIXTURES / "sample_events.json"


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """An 8-second silent H.264 test clip matching fixtures/sample_events.json."""
    path = tmp_path_factory.mktemp("video") / "sample.mp4"
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=8:size=320x240:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path
