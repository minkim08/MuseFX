"""ffmpeg helpers: read a video's length and attach the mixed audio.

The video stream is copied, never re-encoded, so there is no quality loss and
the merge takes about a second.
"""

import re
import shutil
import subprocess
from pathlib import Path

_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_AUDIO_STREAM = re.compile(r"Stream #\d+:\d+.*: Audio:")
# Generated sounds over the clip's own audio, kept under -1 dBFS. The source is
# made stereo first, or mixing a mono clip would lose all the panning.
_MIX_WITH_SOURCE = (
    "[0:a:0]aformat=channel_layouts=stereo[source];"
    "[source][1:a:0]amix=inputs=2:duration=longest:normalize=0,"
    "alimiter=limit=0.89:level=disabled[mixed]"
)


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


def has_audio(video: str | Path) -> bool:
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(video)], capture_output=True, text=True
    )
    return bool(_AUDIO_STREAM.search(result.stderr))


def merge_audio(video: str | Path, audio: str | Path, out: str | Path) -> None:
    """Write out = video's picture + the given audio, mixed over any audio the video has."""
    audio_map = ["-map", "1:a:0"]
    if has_audio(video):
        audio_map = ["-filter_complex", _MIX_WITH_SOURCE, "-map", "[mixed]"]
    result = subprocess.run(
        [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", *audio_map,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg merge failed: {result.stderr.strip()[-500:]}")
