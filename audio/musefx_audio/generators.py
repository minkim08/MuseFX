"""Sound generators: AudioGen on Baseten, or a fake one for offline testing.

Both expose generate(prompts, durations, n_candidates, seed) and return
(sample_rate, candidates), where candidates[i] holds n_candidates mono float32
arrays for prompts[i].
"""

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Protocol

import numpy as np

# Prompts per Baseten request. With 3 candidates this is one GPU batch of 24
# on the server, and keeps each response to a few MB.
PROMPTS_PER_REQUEST = 8


class Generator(Protocol):
    name: str  # cache namespace: sounds from different generators are never shared

    def generate(
        self, prompts: list[str], durations: list[float], n_candidates: int, seed: int
    ) -> tuple[int, list[list[np.ndarray]]]: ...


class BasetenGenerator:
    """Calls the AudioGen Truss in audio/baseten/audiogen over HTTPS."""

    name = "audiogen"

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        environment: str = "production",
        timeout: float = 600,
    ):
        model_id = model_id or os.environ.get("BASETEN_MODEL_ID")
        self._api_key = api_key or os.environ.get("BASETEN_API_KEY")
        if not model_id or not self._api_key:
            raise RuntimeError("Set BASETEN_MODEL_ID and BASETEN_API_KEY (or use --fake).")
        path = "development" if environment == "development" else f"environments/{environment}"
        self._url = f"https://model-{model_id}.api.baseten.co/{path}/predict"
        self._timeout = timeout

    def generate(self, prompts, durations, n_candidates, seed):
        # Sort by duration so each request generates audio only as long as its
        # longest sound, instead of every sound being as long as the longest one.
        order = sorted(range(len(prompts)), key=lambda i: durations[i])
        candidates: list[list[np.ndarray] | None] = [None] * len(prompts)
        sample_rate = 0
        for start in range(0, len(order), PROMPTS_PER_REQUEST):
            chunk = order[start : start + PROMPTS_PER_REQUEST]
            response = self._post({
                "prompts": [prompts[i] for i in chunk],
                "durations": [durations[i] for i in chunk],
                "n_candidates": n_candidates,
                "seed": seed + start,
            })
            sample_rate = int(response["sample_rate"])
            for i, options in zip(chunk, response["candidates"]):
                candidates[i] = [_decode_pcm16(b64) for b64 in options]
        return sample_rate, candidates

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"Baseten returned HTTP {e.code}: {detail}") from e


class FakeGenerator:
    """Deterministic synthetic sounds, so the mixer can be tested with no GPU.

    Each sound has 20-80 ms of leading silence (to exercise trimming). The first
    candidate has a soft 40 ms attack; the others are sharp, so the picker has
    a real choice to make.
    """

    name = "fake"
    sample_rate = 16000

    def __init__(self):
        self.calls = 0

    def generate(self, prompts, durations, n_candidates, seed):
        self.calls += 1
        candidates = [
            [self._sound(prompt, duration, seed + k, soft=(k == 0)) for k in range(n_candidates)]
            for prompt, duration in zip(prompts, durations)
        ]
        return self.sample_rate, candidates

    def _sound(self, prompt: str, duration: float, seed: int, soft: bool) -> np.ndarray:
        digest = hashlib.sha256(f"{prompt}|{seed}".encode()).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
        sr = self.sample_rate
        lead = np.zeros(int(sr * rng.uniform(0.02, 0.08)), dtype=np.float32)
        n = int(sr * (duration + 0.3))
        t = np.arange(n) / sr
        tone = np.sin(2 * np.pi * rng.uniform(120, 900) * t)
        body = (0.6 * tone + 0.4 * rng.standard_normal(n)) * np.exp(-t / rng.uniform(0.05, 0.3))
        if soft:
            body *= np.clip(t / 0.04, 0, 1)
        return np.concatenate([lead, 0.7 * body / np.abs(body).max()]).astype(np.float32)


def _decode_pcm16(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype="<i2").astype(np.float32) / 32768.0
