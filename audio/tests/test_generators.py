import base64
import importlib.util
import io
import itertools
import json
import urllib.error
from pathlib import Path

import numpy as np
import pytest

from musefx_audio import generators
from musefx_audio.generators import BasetenGenerator, RunPodGenerator

ROOT = Path(__file__).resolve().parent.parent


def test_baseten_generator_chunks_sorts_and_decodes(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        sent.append((request.full_url, request.headers["Authorization"], body))
        pcm = lambda d: base64.b64encode((np.full(int(16000 * d), 0.5) * 32767).astype("<i2").tobytes()).decode()
        response = {
            "sample_rate": 16000,
            "candidates": [[pcm(d)] * body["n_candidates"] for d in body["durations"]],
        }
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(generators.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(generators, "PROMPTS_PER_REQUEST", 2)
    gen = BasetenGenerator(model_id="abc123", api_key="test-key")

    durations = [1.0, 0.2, 0.5]
    sr, candidates = gen.generate(["a", "b", "c"], durations, n_candidates=3, seed=0)

    assert sr == 16000
    assert sent[0][0] == "https://model-abc123.api.baseten.co/environments/production/predict"
    assert sent[0][1] == "Bearer test-key"
    assert [body["prompts"] for _, _, body in sent] == [["b", "c"], ["a"]]  # shortest first, 2 per request
    for options, duration in zip(candidates, durations):
        assert len(options) == 3
        assert len(options[0]) == int(16000 * duration)
        assert np.allclose(options[0], 0.5, atol=1e-4)


def http_error(code):
    return urllib.error.HTTPError("https://api.runpod.ai", code, "error", {}, io.BytesIO(b"nope"))


class FakeRunPod:
    """Scripted Runpod API with a fake clock: each sleep advances time instead of waiting."""

    def __init__(self, monkeypatch, polls):
        self.polls = iter(polls)  # one per status request: a status string, a dict, or an exception
        self.submits = 0
        self.cancelled = []
        self.now = 0.0
        monkeypatch.setattr(generators.urllib.request, "urlopen", self.urlopen)

    def generator(self, **kwargs):
        return RunPodGenerator(
            endpoint_id="ep1", api_key="test-key", sleep=self.sleep, clock=lambda: self.now, **kwargs
        )

    def sleep(self, seconds):
        self.now += seconds

    def urlopen(self, request, timeout):
        assert timeout <= 30  # no connection is held open through a cold start
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.full_url.endswith("/run"):
            self.submits += 1
            self.body = json.loads(request.data)
            return reply({"id": "job1", "status": "IN_QUEUE"})
        if "/cancel/" in request.full_url:
            self.cancelled.append(request.full_url.rsplit("/", 1)[-1])
            return reply({"status": "CANCELLED"})
        assert request.full_url == "https://api.runpod.ai/v2/ep1/status/job1"
        poll = next(self.polls)
        if isinstance(poll, Exception):
            raise poll
        return reply(poll if isinstance(poll, dict) else {"status": poll})


def reply(data):
    return io.BytesIO(json.dumps(data).encode())


def completed():
    pcm = base64.b64encode((np.full(1600, 0.5) * 32767).astype("<i2").tobytes()).decode()
    return {"status": "COMPLETED", "output": {"sample_rate": 16000, "candidates": [[pcm, pcm, pcm]]}}


def test_runpod_survives_a_five_minute_cold_start_with_network_blips(monkeypatch):
    # 120 polls at 3 s is 6 minutes queued, with failures mixed in, before the job runs.
    queued = ["IN_QUEUE"] * 120
    queued[10] = urllib.error.URLError("connection reset")
    queued[50] = http_error(503)
    queued[90] = TimeoutError("timed out")
    api = FakeRunPod(monkeypatch, [*queued, "IN_PROGRESS", completed()])
    messages = []

    sr, candidates = api.generator(on_status=messages.append).generate(["boom"], [0.1], 3, 0)

    assert api.now > 300
    assert api.submits == 1  # waiting never resubmits the job
    assert sr == 16000 and len(candidates[0]) == 3
    assert np.allclose(candidates[0][0], 0.5, atol=1e-4)
    assert api.body["input"]["prompts"] == ["boom"]
    assert any("waiting for a GPU worker" in m for m in messages)


def test_runpod_gives_up_and_cancels_after_max_wait(monkeypatch):
    api = FakeRunPod(monkeypatch, itertools.repeat("IN_QUEUE"))
    with pytest.raises(RuntimeError, match="cancelled"):
        api.generator(max_wait=600).generate(["boom"], [0.1], 3, 0)
    assert api.cancelled == ["job1"]
    assert api.now >= 600


@pytest.mark.parametrize(
    "poll, message",
    [
        ({"status": "FAILED", "error": "CUDA out of memory"}, "CUDA out of memory"),
        ({"status": "TIMED_OUT"}, "TIMED_OUT"),
        ({"status": "COMPLETED", "output": {"error": "bad request: boom"}}, "bad request"),
        ({"status": "COMPLETED"}, "no output"),
    ],
)
def test_runpod_job_failures_are_reported(monkeypatch, poll, message):
    api = FakeRunPod(monkeypatch, [poll])
    with pytest.raises(RuntimeError, match=message):
        api.generator().generate(["boom"], [0.1], 3, 0)


def test_runpod_bad_api_key_fails_at_once(monkeypatch):
    def denied(request, timeout):
        raise http_error(401)

    monkeypatch.setattr(generators.urllib.request, "urlopen", denied)
    gen = RunPodGenerator(endpoint_id="ep1", api_key="bad", sleep=lambda s: pytest.fail("retried a 401"))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        gen.generate(["boom"], [0.1], 3, 0)


def test_runpod_needs_credentials(monkeypatch):
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RUNPOD_ENDPOINT_ID"):
        RunPodGenerator()


def test_worker_handler_returns_bad_requests_as_errors():
    spec = importlib.util.spec_from_file_location("runpod_handler", ROOT / "runpod" / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # only defines make_handler; the worker starts under __main__

    class Model:
        def predict(self, request):
            if not request["prompts"]:
                raise ValueError("prompts must be non-empty")
            return {"sample_rate": 16000, "candidates": [[]]}

    handler = module.make_handler(Model())
    assert handler({"input": {"prompts": ["boom"]}})["sample_rate"] == 16000
    assert "error" in handler({"input": {"prompts": []}})
    assert "error" in handler({})
