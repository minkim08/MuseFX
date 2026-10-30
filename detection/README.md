# Detection — Component 1: Video → Events

**Owner:** Person 1 · **Runs on:** Meta Model API (Muse Spark + SAM 3.1) + local/backend `ffmpeg`

Detection turns a video into `events.json`. It is the hardest part, because a sound even 100 ms off feels wrong — timing precision is the priority for this component.

Shared contract and project context: [Agents.md](../Agents.md) · Implementation plan and decision log: [PLAN.md](PLAN.md)

## Status: implemented (local)

Pipeline core (`pipeline.py`), Meta clients (`meta_client.py`), SAM output parser (`sam_parse.py`), contract/post-processing (`events.py`), FastAPI service (`service.py`), and a thin CLI (`cli.py`). Product calls the service over HTTP (`HttpDetection` adapter, enabled with `DETECTION_API_URL`); the CLI is a dev tool.

Run it:

```sh
# from the repo root, once
python3 -m venv .venv && .venv/bin/pip install -r detection/requirements.txt
# service (reads detection/.env for MODEL_API_KEY)
.venv/bin/python -m uvicorn detection.service:app --host 127.0.0.1 --port 8100
# or one-off CLI
.venv/bin/python -m detection.cli clip.mp4 events.json
```

Service API: `POST /detect` (multipart `video`) → `202 {job_id, status, stage, progress, error}`; `GET /detect/{job_id}`; `GET /detect/{job_id}/events` (409 until done); `GET /health`.

Phase 0 findings (live, against the real API): auth/model access works (`muse-spark-1.3`, `sam-3.1`); Muse Spark accepts an uploaded video directly (`input_file`), so the scene pass uses the clip itself rather than extracted keyframes; SAM 3.1 takes one short noun-phrase concept per request (zero matches is a valid result) and returns per-frame box lines — SAM concept wording is literal, so the pipeline retries shape/noun variants on zero matches (e.g. "red rectangle" → "red square"). Frame indices map at the clip's native fps.

## Input / Output

| | |
|---|---|
| **Input** | An MP4 (up to 60 seconds, up to 1080p, from the Backend `POST /jobs`) |
| **Output** | `events.json` — the only handoff to Audio. Schema and field meanings are frozen in [Agents.md](../Agents.md#interface-contract-eventsjson); change only with everyone's agreement |

## Pipeline

### 1. Extract frames

- Run `ffmpeg` at **10 fps** for analysis.
- Keep **full fps** for refining timing later (step 4).

### 2. Understand the scene

- Send keyframes (about **1 per second**) to **Muse Spark**.
- Ask for:
  - The setting (indoor or outdoor, surface type)
  - The main moving objects
  - A list of likely sound events with rough times

### 3. Track objects

- Use **SAM 3.1** to segment and track the objects Muse Spark named — e.g. `"player character"`, `"ball"`, `"door"` — across frames.

### 4. Find exact moments

From each tracked object's motion, detect sharp changes:

| Signal | Event |
|---|---|
| Sudden stop or change of direction | Impact |
| Foot reaching its lowest point | Footstep |
| Two masks touching | Collision |

- Refine each time to the exact frame using full-fps frames, so the sound's attack (`t` in `events.json`) lands on the frame where the action happens.

### 5. Write sound prompts

- For each event, send **Muse Spark 3 frames around it** plus the scene description.
- Ask for: an AudioGen `prompt`, a `duration`, and a `volume`.
- Ask for **JSON output**.

### 6. Set position

- Set `pan` from the object's horizontal position on screen (`-1` = left, `1` = right, per the `events.json` contract).

## Ways to improve quality

- **Merge** events closer together than **80 ms** so sounds don't double up.
- Keep a **fixed list of event types** (`footstep`, `impact`, `hit`, `door`, `pickup`, `ui`, `whoosh`, `other`) so prompts stay consistent; footsteps use the same surface prompt for the whole clip.
- Have **Muse Spark check the final event list against keyframes** and remove false positives.
- Set `confidence` honestly (0–1): Audio can skip events below 0.5, so low-confidence guesses should be marked, not hidden.

## Fallback

If tracking is unreliable: have Muse Spark pick events straight from frames at 10 fps, with lower timing precision.

Ship the fallback rather than no output — a roughly-timed event list still lets Audio and Product demo end to end.

## Open questions

Verify against the Meta Model API before writing pipeline code. Official docs: https://dev.meta.ai/docs/overview

- [x] Whether Muse Spark accepts video directly or only images — video is accepted directly ([Video understanding](https://dev.meta.ai/docs/video-understanding)); whether to use video or 1 fps keyframes for the scene pass is a cost/accuracy choice to make in the Phase 0 spike
- [ ] Request size limits (frames per request, image size, payload size) — check the File handling and Pricing/rate limits docs during the spike
- [x] How SAM 3.1 takes text prompts — name the object in a short text phrase; SAM 3.1 segments images and video through the Model API ([Media segmentation](https://dev.meta.ai/docs/media-segmentation)). Exact request/response shapes still to confirm with a real call

Auth: `MODEL_API_KEY` env var, Authorization header at `https://api.meta.ai/v1` ([Authentication](https://dev.meta.ai/docs/authentication)). Key lives in the local gitignored `detection/.env` — never commit or print it.

## Testing this component alone

- Run Detection on one sample gameplay clip and inspect the emitted `events.json` by hand: are `t` values on the exact action frames (foot down, contact, stop)?
- Until the Backend exists, Detection can be run standalone on a local MP4; Audio and Product test in parallel against a hand-written `events.json` for the same sample clip, so no one waits on this component.
