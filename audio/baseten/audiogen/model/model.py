"""Baseten Truss serving AudioGen for MuseFX.

Request:  {"prompts": [str], "durations": [float], "n_candidates": 3, "seed": 0}
Response: {"sample_rate": 16000, "candidates": [[base64 PCM16 mono, ...] per prompt]}

All prompts x candidates run as GPU batches in one request, generated at the
longest requested duration plus a little room for leading silence (which the
mixer trims), then each is cut to its own duration.
"""

import base64

import numpy as np
import torch
from audiocraft.models import AudioGen

WEIGHTS_DIR = "/models/audiogen-medium"
MAX_BATCH = 24
MAX_SECONDS = 10.0
PAD_SECONDS = 0.5
MAX_CANDIDATES = 5


class Model:
    def __init__(self, **kwargs):
        self._model = None

    def load(self):
        self._model = AudioGen.get_pretrained(WEIGHTS_DIR, device="cuda")

    def predict(self, request: dict) -> dict:
        prompts = [str(p) for p in request["prompts"]]
        durations = [float(d) for d in request["durations"]]
        n = int(request.get("n_candidates", 3))
        seed = int(request.get("seed", 0))
        if not prompts or len(prompts) != len(durations):
            raise ValueError("prompts and durations must be non-empty and the same length")
        if not 1 <= n <= MAX_CANDIDATES:
            raise ValueError(f"n_candidates must be between 1 and {MAX_CANDIDATES}")

        length = min(max(durations) + PAD_SECONDS, MAX_SECONDS)
        self._model.set_generation_params(duration=length)
        torch.manual_seed(seed)

        descriptions = [p for p in prompts for _ in range(n)]
        outputs = []
        for start in range(0, len(descriptions), MAX_BATCH):
            with torch.inference_mode():
                wav = self._model.generate(descriptions[start : start + MAX_BATCH])  # [B, 1, T]
            outputs.extend(wav[:, 0].float().cpu().numpy())

        sr = self._model.sample_rate
        candidates = []
        for i, duration in enumerate(durations):
            keep = int(sr * min(duration + PAD_SECONDS, MAX_SECONDS))
            candidates.append([_encode_pcm16(outputs[i * n + k][:keep]) for k in range(n)])
        return {"sample_rate": sr, "candidates": candidates}


def _encode_pcm16(audio: np.ndarray) -> str:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    return base64.b64encode(pcm.tobytes()).decode()
