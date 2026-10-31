"""ffmpeg helpers: read a video's length and attach the mixed audio.

The video stream is copied, never re-encoded, so there is no quality loss and
the merge takes about a second.
"""

import re
import shutil
import subprocess
from pathlib import Path

_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")


def ffmpeg_exe() -> str:
    """System ffmpeg if installed, else the one bundled with imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError as e:
        raise RuntimeError("ffmpeg not found: install it or `pip install imageio-ffmpeg`") from e
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_duration(video: str | Path) -> float:
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(video)], capture_output=True, text=True
    )
    match = _DURATION.search(result.stderr)
    if not match:
        raise RuntimeError(f"Could not read the duration of {video}: {result.stderr.strip()[-300:]}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def merge_audio(video: str | Path, audio: str | Path, out: str | Path) -> None:
    """Write out = video's picture + the given audio, replacing any audio the video had."""
    result = subprocess.run(
        [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg merge failed: {result.stderr.strip()[-500:]}")
