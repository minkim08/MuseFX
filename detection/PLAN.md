# Detection — Implementation Plan

Status: implemented locally (Phases 0–6; timing bar on labelled gameplay clips still unmeasured). Shared contract: [Agents.md](../Agents.md) · Component overview: [README.md](README.md)

## Goal

Build Detection (Person 1) as a locally runnable component that turns an MP4 into a contract-valid `events.json`, using the Meta Model API (Muse Spark + SAM 3.1) from this machine. Baseten comes later and is a non-goal here, but the design must not paint us into a corner for it.

## Success Criteria

- `detection/` runs as its own API service: submit an MP4, poll job status, fetch `events.json`. A thin CLI wraps the same pipeline core for local testing and fixtures, but the API is the integration contract.
- The service reports stage/progress so Product's `detecting`/`describing` stages reflect real work instead of sitting at one milestone during a single blocking call.
- Output passes the frozen contract as enforced by Product: schema in [Agents.md](../Agents.md#interface-contract-eventsjson), semantic rules in Product's `models.rs`, example in `product/docs/events.example.json`.
- Works on the sample clip plus real gameplay clips; event times land within 100 ms of the real moment on labelled test clips (team contract bar).
- Plugs into Product by replacing `MockDetection` without changing routes, schema, or frontend.
- No keys, weights, footage, or generated media committed.

## What we need (keys and access)

Don't paste secrets in chat. Place them in a local, gitignored `.env` (a `detection/.env.example` will list names only):

1. **A Meta Model API key** from a Meta developer account — the single credential the local build needs. Official env name is `MODEL_API_KEY` (Authorization header at `https://api.meta.ai/v1`, per the Authentication docs below). Status: key provided locally in `detection/.env` (gitignored, value never committed or printed).
2. **Confirmation that Muse Spark and SAM 3.1 are enabled** on that account/key.
3. Anything else the portal shows as required for API calls (org/project ID, base URL if not the default).

If any of that doesn't exist yet, Phase 0 starts with a spike to find out.

## Approach

Python in `detection/`, project `.venv` at the repo root: a pipeline core library, a FastAPI service exposing it, and a thin CLI over the same core for local runs and fixture generation. The API is the product-facing interface; the CLI is a dev tool, not the integration contract. `ffmpeg`/`ffprobe` via stdlib first; add Python deps only when a step needs one. All model access sits behind two small client interfaces (`MuseSparkClient`, `SamClient`) that read base URL, key, and model names from env. That seam is what makes Baseten a later adapter/config change instead of a rewrite, and it lets tests run offline against recorded API responses.

Service shape (mirrors Product's job style): `POST /detect` (multipart MP4) → `202 {job_id, status, stage, progress, error}`; `GET /detect/{job_id}` → current status; `GET /detect/{job_id}/events` → `events.json` (`409` until done). Keys and model config live server-side in the service's env only.

Pipeline (from [README.md](README.md)): ffprobe metadata → ffmpeg frames (10 fps analysis, full fps retained) → Muse Spark scene pass on ~1 fps keyframes → SAM 3.1 tracking of the named objects → exact-moment refinement from mask motion at full fps → Muse Spark prompt writing (3 frames per event, JSON out) → `pan` from mask centre (`2·x/width − 1`) → post-processing (merge <80 ms, clamp events ending past clip end, map model output onto the 8 fixed types, honest `confidence`) → validated `events.json`. Fallback: if tracking fails, Muse Spark picks events from 10 fps frames at lower precision rather than emitting nothing.

Contract traps handled in post-processing: closed type enum, `t + duration ≤ video.duration`, adapter return value must equal the written file, and input limits (60 s / 1080p) re-checked here because they currently live inside Product's mock.

## Steps

1. **Phase 0 — API spike.** With the key, make the smallest real calls: Muse Spark with 2–3 keyframes (image vs video input, size limits), SAM 3.1 with one text prompt on a short clip (prompt format, mask/track response shape, sync vs polling). Save redacted responses as test fixtures; record answers in the Open Questions section of [README.md](README.md). Exit gate: auth, endpoints, payload shapes, and limits known from evidence.
2. **Scaffold + offline core.** Pipeline core, FastAPI service skeleton with the job endpoints above, thin CLI entrypoint, ffprobe/ffmpeg stage, `events.json` writer, and a local validator mirroring Product's `validate()` rules; pytest unit tests covering merging, clamping, type mapping, pan, and the service's submit/poll/fetch flow. Runs with no API key using fixture responses.
3. **Muse Spark scene + prompt stages.** Scene pass and per-event prompt writing via the client interface; structured JSON parsed defensively (retry once, then fallback event with low confidence).
4. **SAM 3.1 tracking + timing refinement.** Track named objects, derive candidate moments (stop/direction change, foot lowest point, mask contact), refine at full fps, emit events.
5. **Standalone validation.** Run on the generated sample and on gameplay clips; validate output with Product's own checker. Build a tiny timing check: hand-labelled moments on up to 5 test clips vs emitted `t`, report max error. Dependency: test clips + labels (Person 3 owns clips — flag if missing).
6. **Product integration (local).** Rust `DetectionAdapter` implemented as an HTTP client for the Detection service (add an HTTP client dependency to the backend, e.g. `reqwest`): submits Product's `original.mp4`, polls status with a timeout, fetches `events.json`, writes it to the job folder, and returns the parsed document (so Product's file-equality and validation checks pass unchanged). Service URL from env (`DETECTION_API_URL`); wired in `main.rs` behind env selection so the mock remains the default. Verify upload → done in the running app, with Detection stages progressing in the UI.
7. **Docs close-out.** Update [README.md](README.md) (run instructions, env vars, recorded API answers) and sync [Agents.md](../Agents.md) if Detection status changed.

## Validation Plan

- Per stage: pytest unit tests with recorded fixtures (no network, no key needed).
- Contract: emitted `events.json` accepted by Product's Rust validation (existing events tests + a stub-adapter API test with real service output as fixture).
- Timing: labelled-clip check from Step 5; report measured error, don't claim the 100 ms bar without it.
- End to end: sample clip and one gameplay clip through the live Product app; job reaches `done`, timeline markers seek correctly.
- Hygiene: `git status` shows no `.env`, keys, weights, frames, or footage (covered by `.gitignore`).

## Risks

- Meta Model API is the biggest unknown: Muse Spark media input/limits and SAM 3.1's hosted prompt/mask interface are unverified until Phase 0 — the plan is deliberately spike-first.
- Latency/cost of per-frame API calls on a 60 s clip is unknown; keyframe and candidate counts may need tuning after the spike.
- Mask-motion footstep/impact heuristics are the weakest link for the 100 ms bar; the 10 fps Muse-only fallback is the safety net.
- Baseten: deferred. Client-interface seam keeps the door open; no Baseten-specific design now.

## Decisions

### Decided

| Decision | Choice | Why |
|---|---|---|
| Hosting (for now) | Meta Model API, called locally | Team direction; Baseten later |
| Language | Python in `detection/` (core library + FastAPI service + thin CLI) | Fastest iteration for vision/API work; service is the interface, CLI is for local dev and fixtures |
| Model access | `MuseSparkClient` / `SamClient` interfaces, config from env | Baseten becomes an adapter swap later; tests run offline on fixtures |
| Integration | Detection is its own API service (`POST /detect`, poll, fetch `events.json`); Product's Rust adapter is an HTTP client for it, mock stays the default behind env selection (`DETECTION_API_URL`) | Components stay independently owned/deployable; async jobs give real stage progress; CLI subprocess coupling (shared paths, one machine) rejected as the integration contract |
| Fallback | Muse Spark picks events from 10 fps frames if tracking fails | A rough event list beats no output for the demo |
| Secrets | Keys only in local gitignored `.env`; `.env.example` lists names | Never commit credentials |

### Future decisions (open)

| # | Decision | Decide when | Options / notes |
|---|---|---|---|
| D1 | Model IDs for Muse Spark and SAM 3.1 | Phase 0 spike | Env name (`MODEL_API_KEY`) and base URL (`https://api.meta.ai/v1`) confirmed from docs; Muse Spark listed as `muse-spark-1.1`/`1.2`/`1.3` — confirm which the key can call and pick a default |
| D2 | Muse Spark media input shape | Phase 0 spike | Docs confirm direct video upload is supported (Video understanding); still confirm size/frame limits and whether video or 1 fps keyframes is cheaper/more accurate for the scene pass |
| D3 | SAM 3.1 response handling | Phase 0 spike | Mask/track format, sync vs polling, how much mask data to persist as fixtures |
| D4 | Event ending past clip end | Before Step 2 validator is finalised | Clamp `duration` in Detection (current plan) vs relax Product's `t + duration ≤ duration` rule — contract change needs everyone's agreement |
| D5 | Who emits `ambience` | Step 3 | Muse Spark scene pass proposes the bed prompt/volume, or leave `ambience` unset until Audio asks for it |
| D6 | Test clips and ground-truth labels | Before Step 5 | Person 3 owns clips per team contract; 5 clips with hand-labelled moments needed to measure the 100 ms bar |
| D7 | API budget/latency tuning | After first end-to-end run | Keyframe rate, candidates per event, caching; depends on observed cost per clip |
| D8 | Baseten migration | Later, explicitly out of scope now | Which models move, transport (hosted endpoint vs self-host), and whether the client seam is enough |
| D9 | Service auth between Product and Detection | Before any non-local deployment | Local: none on localhost. Deployed: shared token via env on both sides; no user auth (app has no accounts) |
| D10 | Detection service hosting | Later, with Product hosting | Local process for now; host choice (with or near Product) deferred until Product hosting is decided |

## Sources

- [Meta Model API — Overview](https://dev.meta.ai/docs/overview) (official docs hub; provided by the team)
- [Meta Model API — Authentication](https://dev.meta.ai/docs/authentication) (`MODEL_API_KEY`, Authorization header, `https://api.meta.ai/v1`)
- [Meta Model API — Video understanding](https://dev.meta.ai/docs/video-understanding) (Muse Spark accepts video directly)
- [Meta Model API — Media segmentation](https://dev.meta.ai/docs/media-segmentation) (SAM 3.1 segments images and video from a short text prompt)
- [Meta SAM 3 / SAM 3.1 announcement](https://ai.meta.com/blog/segment-anything-model-3/)
