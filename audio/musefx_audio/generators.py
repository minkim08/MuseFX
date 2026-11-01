"""Sound generators: AudioGen on Runpod or Baseten, or a fake one for offline testing.

Both expose generate(prompts, durations, n_candidates, seed) and return
(sample_rate, candidates), where candidates[i] holds n_candidates mono float32
arrays for prompts[i].
"""

import base64
import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

import numpy as np

# Prompts per request. With 3 candidates this is 24 sounds, and keeps each
# response to a few MB.
PROMPTS_PER_REQUEST = 8

# How long to keep waiting for one Runpod job. Cold starts count towards this,
# so it is set well above the longest one we expect (about 5 minutes).
RUNPOD_MAX_WAIT_SECONDS = 1800
RUNPOD_EXECUTION_TIMEOUT_MS = 900_000  # generation only, after a worker picks up the job
RUNPOD_POLL_SECONDS = 3
RUNPOD_REQUEST_TIMEOUT = 30
RUNPOD_REPORT_EVERY_SECONDS = 30


class Generator(Protocol):
    name: str  # cache namespace: sounds from different generators are never shared

    def generate(
        self, prompts: list[str], durations: list[float], n_candidates: int, seed: int
    ) -> tuple[int, list[list[np.ndarray]]]: ...


class _RemoteGenerator:
    """Splits prompts into requests; subclasses send each one with _predict."""

    name = "audiogen"

    def generate(self, prompts, durations, n_candidates, seed):
        # Sort by duration so each request generates audio only as long as its
        # longest sound, instead of every sound being as long as the longest one.
        order = sorted(range(len(prompts)), key=lambda i: durations[i])
        candidates: list[list[np.ndarray] | None] = [None] * len(prompts)
        sample_rate = 0
        for start in range(0, len(order), PROMPTS_PER_REQUEST):
            chunk = order[start : start + PROMPTS_PER_REQUEST]
            response = self._predict({
                "prompts": [prompts[i] for i in chunk],
                "durations": [durations[i] for i in chunk],
                "n_candidates": n_candidates,
                "seed": seed + start,
            })
            sample_rate = int(response["sample_rate"])
            for i, options in zip(chunk, response["candidates"]):
                candidates[i] = [_decode_pcm16(b64) for b64 in options]
        return sample_rate, candidates

    def _predict(self, body: dict) -> dict:
        raise NotImplementedError


class _Transient(Exception):
    """A network blip or server hiccup that is worth retrying."""


class RunPodGenerator(_RemoteGenerator):
    """Calls the AudioGen worker in audio/runpod.

    A job is submitted and then polled, so no connection stays open while a GPU
    worker starts. A slow cold start only means more polls, and network blips
    during the wait are retried.
    """

    def __init__(
        self,
        endpoint_id: str | None = None,
        api_key: str | None = None,
        max_wait: float = RUNPOD_MAX_WAIT_SECONDS,
        on_status: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        endpoint_id = endpoint_id or os.environ.get("RUNPOD_ENDPOINT_ID")
        self._api_key = api_key or os.environ.get("RUNPOD_API_KEY")
        if not endpoint_id or not self._api_key:
            raise RuntimeError("Set RUNPOD_ENDPOINT_ID and RUNPOD_API_KEY (or use --fake).")
        self._base = f"https://api.runpod.ai/v2/{endpoint_id}"
        self._max_wait = max_wait
        self._on_status = on_status or (lambda message: None)
        self._sleep = sleep
        self._clock = clock

    def _predict(self, body: dict) -> dict:
        started = self._clock()
        deadline = started + self._max_wait
        payload = {"input": body, "policy": {"executionTimeout": RUNPOD_EXECUTION_TIMEOUT_MS}}
        job_id = self._submit(payload, deadline)

        last_report = started
        while True:
            try:
                state = self._request(f"{self._base}/status/{job_id}")
            except _Transient:
                state = {}
            status = state.get("status")
            if status == "COMPLETED":
                output = state.get("output") or {}
                if "error" in output or "candidates" not in output:
                    raise RuntimeError(f"Runpod job {job_id} failed: {output.get('error', 'no output')}")
                return output
            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                raise RuntimeError(f"Runpod job {job_id} {status}: {state.get('error', 'see the endpoint logs')}")

            now = self._clock()
            if now >= deadline:
                self._cancel(job_id)
                raise RuntimeError(
                    f"Runpod job {job_id} was not done after {self._max_wait:.0f} s, so it was cancelled. "
                    "Check the endpoint's workers and logs."
                )
            if now - last_report >= RUNPOD_REPORT_EVERY_SECONDS:
                last_report = now
                activity = "generating" if status == "IN_PROGRESS" else "waiting for a GPU worker"
                self._on_status(f"{activity} ({now - started:.0f} s so far)")
            self._sleep(RUNPOD_POLL_SECONDS)

    def _submit(self, payload: dict, deadline: float) -> str:
        while True:
            try:
                return self._request(f"{self._base}/run", payload)["id"]
            except _Transient as e:
                if self._clock() >= deadline:
                    raise RuntimeError(f"Could not reach Runpod: {e}") from e
                self._sleep(RUNPOD_POLL_SECONDS)

    def _cancel(self, job_id: str) -> None:
        """Best effort, so an abandoned job stops costing money."""
        try:
            self._request(f"{self._base}/cancel/{job_id}", {})
        except (_Transient, RuntimeError):
            pass

    def _request(self, url: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            url,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="GET" if body is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=RUNPOD_REQUEST_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 429 or e.code >= 500:
                raise _Transient(f"HTTP {e.code}") from e
            raise RuntimeError(f"Runpod returned HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, http.client.HTTPException, json.JSONDecodeError, OSError) as e:
            raise _Transient(str(e)) from e


class BasetenGenerator(_RemoteGenerator):
    """Calls the AudioGen Truss in audio/baseten/audiogen over HTTPS."""

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

    def _predict(self, body: dict) -> dict:
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
