"""Detection service: its own API (see PLAN.md).

POST /detect (multipart MP4) -> 202 {job_id, status, stage, progress, error}
GET  /detect/{job_id}        -> job status
GET  /detect/{job_id}/events -> events.json (409 until done)
GET  /health                 -> {status, ffmpeg, model_api_key_configured}
"""
from __future__ import annotations

import os
import pathlib
import threading
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .meta_client import load_env
from .pipeline import detect, review_result

load_env()
DATA = pathlib.Path(os.environ.get("DETECTION_DATA_DIR", pathlib.Path(__file__).parent / "data" / "jobs"))
app = FastAPI(title="MuseFX Detection")
jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _job(job_id: str) -> dict:
    return {"job_id": job_id, "status": "queued", "stage": "detecting", "progress": 0, "error": None}


def _run(job_id: str, video: pathlib.Path) -> None:
    def progress(stage: str, pct: int) -> None:
        with _lock:
            jobs[job_id].update(stage=stage, progress=pct)

    with _lock:
        jobs[job_id].update(status="running")
    try:
        result = detect(video, video_id=job_id, progress=progress)
        (video.parent / "events.json").write_text(__import__("json").dumps(result, indent=2))
        with _lock:
            jobs[job_id].update(status="completed", stage="done", progress=100, _result=result)
    except Exception as e:  # surface actionable errors to pollers
        with _lock:
            jobs[job_id].update(status="failed", stage="failed", error=str(e))


@app.get("/health")
def health() -> dict:
    import shutil
    return {
        "status": "ok",
        "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "model_api_key_configured": bool(os.environ.get("MODEL_API_KEY")),
    }


@app.post("/detect", status_code=202)
async def submit(video: UploadFile = File(...)) -> dict:
    if not (video.filename or "").lower().endswith(".mp4"):
        raise HTTPException(415, "Please upload an MP4 video.")
    job_id = str(uuid.uuid4())
    folder = DATA / job_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "original.mp4"
    data = await video.read()
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    target.write_bytes(data)
    with _lock:
        jobs[job_id] = _job(job_id)
    threading.Thread(target=_run, args=(job_id, target), daemon=True).start()
    job = dict(jobs[job_id]); job.pop("_result", None)
    return job


@app.get("/detect/{job_id}")
def status(job_id: str) -> dict:
    with _lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    out = dict(job); out.pop("_result", None)
    return out


@app.post("/review")
async def review(video: UploadFile = File(...), events: str = Form(...)) -> dict:
    """Second pass: Muse Spark watches the finished video+audio, returns per-event verdicts."""
    import json as _json
    import tempfile
    try:
        events_doc = _json.loads(events)
    except Exception:
        raise HTTPException(400, "events must be a JSON string.")
    data = await video.read()
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "result.mp4"
        path.write_bytes(data)
        try:
            verdicts = review_result(path, events_doc)
        except Exception as e:
            raise HTTPException(502, f"Review failed: {e}")
    return {"verdicts": verdicts}


@app.get("/detect/{job_id}/events")
def events(job_id: str):
    with _lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job["status"] != "completed":
        return JSONResponse(status_code=409, content={"detail": job.get("error") or "Result is not ready yet."})
    return job["_result"]
