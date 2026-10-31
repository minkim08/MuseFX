import base64
import io
import json

import numpy as np

from musefx_audio import generators
from musefx_audio.generators import BasetenGenerator


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
