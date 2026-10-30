"""Meta Model API clients (Muse Spark + SAM 3.1) over stdlib HTTP.

Docs: https://dev.meta.ai/docs/overview — Responses API at
https://api.meta.ai/v1, MODEL_API_KEY bearer auth. No third-party deps so
the clients can move to another host (e.g. Baseten) without repackaging.
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.request
import uuid

BASE_URL = os.environ.get("META_MODEL_BASE_URL", "https://api.meta.ai/v1")
MUSE_MODEL = os.environ.get("MUSE_SPARK_MODEL", "muse-spark-1.3")
SAM_MODEL = os.environ.get("SAM_MODEL", "sam-3.1")


def load_env(path: pathlib.Path | None = None) -> None:
    """Load detection/.env into os.environ (existing env wins)."""
    env_path = path or pathlib.Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


class MetaError(RuntimeError):
    pass


class MetaClient:
    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL):
        load_env()
        self.api_key = api_key or os.environ.get("MODEL_API_KEY", "")
        if not self.api_key:
            raise MetaError("MODEL_API_KEY is not set (see detection/.env.example).")
        self.base_url = base_url.rstrip("/")

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.api_key}"}
        h.update(extra or {})
        return h

    def _post_json(self, path: str, payload: dict, timeout: int = 600) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise MetaError(f"Meta API {path} failed ({e.code}): {e.read()[:400]!r}") from e

    def upload_file(self, path: pathlib.Path) -> str:
        boundary = "----musefx" + uuid.uuid4().hex
        data = path.read_bytes()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nuser_data\r\n".encode()
            + f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\nContent-Type: video/mp4\r\n\r\n".encode()
            + data + b"\r\n" + f"--{boundary}--\r\n".encode()
        )
        req = urllib.request.Request(
            self.base_url + "/files", data=body,
            headers=self._headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read())["id"]
        except urllib.error.HTTPError as e:
            raise MetaError(f"Meta API /files failed ({e.code}): {e.read()[:400]!r}") from e

    @staticmethod
    def _output_text(response: dict) -> str:
        texts: list[str] = []
        for item in response.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            for c in item.get("content", []) or []:
                if isinstance(c, dict) and c.get("type") in ("output_text", "text") and c.get("text"):
                    texts.append(c["text"])
        return "\n".join(texts)

    def muse(self, prompt: str, file_id: str | None = None,
             image_data_urls: list[str] | None = None, model: str = MUSE_MODEL) -> str:
        content: list[dict] = [{"type": "input_text", "text": prompt}]
        if file_id:
            content.append({"type": "input_file", "file_id": file_id})
        for url in image_data_urls or []:
            content.append({"type": "input_image", "image_url": url})
        resp = self._post_json("/responses", {
            "model": model,
            "input": [{"type": "message", "role": "user", "content": content}],
        })
        return self._output_text(resp)

    def sam_track_text(self, concept: str, file_id: str) -> str:
        """Stream SAM 3.1 on an uploaded video; return raw output_text."""
        payload = {
            "model": SAM_MODEL, "stream": True,
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_video", "file_id": file_id},
            ]}],
        }
        req = urllib.request.Request(
            self.base_url + "/responses",
            data=json.dumps(payload).encode(),
            headers=self._headers({"Content-Type": "application/json", "Accept": "text/event-stream"}),
            method="POST",
        )
        deltas: list[str] = []
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    ev = json.loads(data)
                    if ev.get("type") == "response.output_text.delta" and ev.get("delta"):
                        deltas.append(ev["delta"])
        except urllib.error.HTTPError as e:
            raise MetaError(f"Meta API SAM failed ({e.code}): {e.read()[:400]!r}") from e
        return "".join(deltas)
