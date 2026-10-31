"""Cache of picked sounds, keyed by prompt and duration.

Repeated prompts (every footstep on the same surface) reuse one generated
sound; the mixer varies pitch and volume so repeats don't sound robotic.
"""

import hashlib
from pathlib import Path

import numpy as np
import soundfile as sf


class SoundCache:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(prompt: str, duration: float) -> str:
        return hashlib.sha256(f"{prompt.strip().lower()}|{duration:.2f}".encode()).hexdigest()[:24]

    def get(self, prompt: str, duration: float) -> tuple[np.ndarray, int] | None:
        path = self._path(prompt, duration)
        if not path.exists():
            return None
        audio, sample_rate = sf.read(path, dtype="float32")
        return audio, sample_rate

    def put(self, prompt: str, duration: float, audio: np.ndarray, sample_rate: int) -> None:
        path = self._path(prompt, duration)
        tmp = path.with_suffix(".tmp.wav")
        sf.write(tmp, audio, sample_rate, subtype="FLOAT")
        tmp.replace(path)  # atomic, so a crash never leaves a half-written sound

    def _path(self, prompt: str, duration: float) -> Path:
        return self.directory / f"{self.key(prompt, duration)}.wav"
