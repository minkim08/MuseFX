# Product — Component 3: App and Integration

Next.js + TypeScript frontend and Rust/Axum backend for MuseFX. This local MVP uses replaceable adapters: real Detection (Python/FastAPI service calling Muse Spark + SAM 3.1) when `DETECTION_API_URL` is set, Mock Detection otherwise; Audio is still Mock Audio (880 Hz test tones, no real sound generation). No Modal deployment is included.

The Rust backend follows the user's requested stack change. The shared [`events.json` and stage contract](../Agents.md) is preserved: Product stores and passes Detection's document through to Audio without translating its schema. Detection and Audio remain independently owned components.

## Run locally

Install stable Rust/Cargo (recent: 1.82 is too old for the locked dependencies), Node.js 22+, and FFmpeg/ffprobe. On macOS:

```sh
brew install rust node@22 ffmpeg
```

From the repository root, start the backend:

```sh
cd product/backend
cargo run
```

In another terminal:

```sh
cd product/frontend
npm install
npm run dev
```

Open http://localhost:3000. Dependency health is at http://localhost:8000/health.

Generate a valid silent H.264 sample MP4 from the repository root:

```sh
bash product/scripts/make_sample.sh
```

Upload `sample.mp4`. Mock Detection emits an impact event at `min(1 second, duration / 3)`; Mock Audio adds a short 880 Hz tone at that time while copying the original video stream and retaining source audio. The sample generator produces a four-second clip, so its event is at one second. This is a test tone rather than real sound generation.

## Features

- Streamed MP4 uploads, up to 100 MB. Mock Detection validates the team limits of 60 seconds and 1080p (including portrait equivalents).
- Polling through `detecting`, `describing`, `generating`, `mixing`, `done`, or `failed`, with monotonic milestone progress. Fast mock stages may pass between polls.
- Original/result playback, event markers, clickable event rows, and downloads.
- Readable validation/pipeline errors, connection timeouts, and manual reconnection.
- Local JSON metadata with atomic writes. Interrupted jobs are marked failed on startup.

## API

| Route | Response |
| --- | --- |
| `POST /jobs` | Multipart field `video`; HTTP 202 job with `job_id`, `status`, `stage`, `progress`, `error` |
| `GET /jobs/{id}` | Current job metadata |
| `GET /jobs/{id}/result` | `original_video_url`, `result_video_url`, `events_url`, parsed `events`; HTTP 409 until complete |
| `GET /jobs/{id}/download/result.mp4` | MP4 attachment |
| `GET /jobs/{id}/download/events.json` | Events attachment |
| `GET /health` | `ok`/`degraded` with FFmpeg/ffprobe availability |
| `/media/{id}/…` | Local artifacts with byte-range requests for playback |

Artifact URLs are relative to the backend origin. API errors have a JSON `detail` message. A corrupt `.mp4` becomes a failed job during Detection. Invalid extensions, empty uploads, and oversized uploads return 415, 400, and 413 respectively; missing multipart `video` returns 422. The total request limit is 101 MB to include multipart overhead.

```sh
curl -F video=@sample.mp4 http://localhost:8000/jobs
curl http://localhost:8000/jobs/JOB_ID
curl http://localhost:8000/jobs/JOB_ID/result
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_DIR` | `product/backend/data/jobs` | Uploads, metadata, events, and result artifacts |
| `BIND_ADDR` | `127.0.0.1:8000` | Backend listener |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | Allowed CORS origin |
| `RUST_LOG` | `video_to_sfx=info,tower_http=info` | Backend logs |
| `DETECTION_API_URL` | _(unset = mock)_ | Detection service base URL (e.g. `http://127.0.0.1:8100`); enables the real `HttpDetection` adapter |
| `AUDIO_DIR` | _(unset = mock)_ | Path to the `audio/` folder (e.g. `../../audio`); enables the real `PythonAudio` adapter. Needs `audio/.venv` |
| `AUDIO_FAKE` | _(unset)_ | Set to `1` to use synthetic sounds through the real Audio pipeline, with no GPU |
| `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY` | _(unset)_ | Required by real Audio unless `AUDIO_FAKE=1` |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Frontend API origin |

Export backend variables before `cargo run`; `.env` files are not automatically loaded. Frontend configuration can use `frontend/.env.local`; examples are included in both components.

## Validation

From the repository root:

```sh
cd product/backend
cargo fmt --check
cargo test
cargo clippy --all-targets -- -D warnings
# Explicit integration test with real FFmpeg-generated MP4s:
cargo test --test api -- --ignored

cd ../frontend
npm run typecheck
npm run build
```

Tests cover the frozen event schema, API success/failure, rejected uploads, CORS, restart recovery, and invalid adapter output. The explicit FFmpeg test checks clips with and without source audio, video frame preservation, the audible tone, event artifacts, downloads, and range playback.

The authoring sandbox currently lacks Cargo/rustc and FFmpeg, and package registry access is blocked. Compilation, tests, Clippy, formatting, and browser playback are **not yet verified**. Manifest/JSON parsing and sample-script shell syntax have been checked. Run the commands above on a configured host before merging.

## Integration and limits

See [adapter handoff](docs/INTEGRATION.md), [example events](docs/events.example.json), and [implementation conventions](AGENTS.md). Traits are injected via `AppState`; Product does not change the shared event document. Mock Audio respects event volume/pan and skips confidence below 0.5; it generates only event test tones, with ambience passed through for the real Audio adapter.

This iteration runs one backend process, with local artifacts and in-process Tokio tasks. Files are unauthenticated; stop the backend before deleting `backend/data/jobs` to clear them. Durable queues, automatic cleanup, cancellation, real model integration, and hosting are future work. Modal deployment from the original design is not implemented by this local Rust MVP.
