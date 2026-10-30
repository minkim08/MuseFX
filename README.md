# MuseFX

AI pipeline that detects key actions in silent gameplay footage and generates perfectly synced sound effects.

Upload a silent clip (MP4, up to 60 seconds, up to 1080p), watch the pipeline stages run, then compare the original and the result side by side — with event markers on a timeline and a downloadable finished MP4.

## Current status: local Product MVP

The Product layer is implemented and runnable locally:

- **Frontend:** Next.js + TypeScript — one page with upload, staged progress bar, side-by-side players, event timeline, and downloads.
- **Backend:** Rust + Axum/Tokio — job API, background pipeline, local artifact storage.
- **Detection is real.** A Python/FastAPI service in [detection/](detection/README.md) calls Muse Spark and SAM 3.1 through the Meta Model API and returns contract-valid `events.json`. Product uses it when `DETECTION_API_URL` is set; otherwise it falls back to Mock Detection (a single impact event at `min(1 second, duration / 3)`).
- **Audio is still a mock.** Mock Audio does not generate real sound effects yet: it adds a short **880 Hz test tone at each event time**, respecting the event's volume and pan, skipping events with confidence below 0.5, and copying the video stream unchanged. It is a timing placeholder for the real Audio component (AudioGen on Modal), which is not built yet.
- **No deployment yet.** Everything runs locally; no Modal deployment is included.

Adapters sit behind `DetectionAdapter` / `AudioAdapter` traits and follow the shared `events.json` contract, so components can be swapped without changing the API, schema, or UI. Full details: [Product README](product/README.md) and [adapter handoff](product/docs/INTEGRATION.md).

## Run it locally

Prerequisites: stable Rust/Cargo, Node.js 22+, and FFmpeg/ffprobe. On macOS:

```sh
brew install rust node@22 ffmpeg
```

Start the backend (from the repository root):

```sh
cd product/backend
cargo run
```

Leave it running at http://localhost:8000. Check it from another terminal:

```sh
curl http://localhost:8000/health
```

Both FFmpeg dependencies should report available.

Start the frontend in a second terminal:

```sh
cd product/frontend
npm install
npm run dev
```

Open http://localhost:3000.

Generate a sample clip and try it:

```sh
# from the repository root
bash product/scripts/make_sample.sh
```

This creates a four-second silent `sample.mp4`. Upload it in the web app; the mock pipeline produces a result with a short test tone at one second.

## How it fits together

| Part | Owner | Handoff |
|---|---|---|
| Detection | Person 1 | `events.json` for any clip |
| Audio | Person 2 | Finished MP4 with sound |
| Product | Person 3 | Live app and demo video |

All three parts connect through one frozen contract, `events.json`. See [Agents.md](Agents.md) for the shared project context, schema, and team contract, and the component READMEs for [Detection](detection/README.md), [Audio](audio/README.md), and [Product](product/README.md).

## Licence

The MIT licence in [LICENSE](LICENSE) covers MuseFX's own code only. It does not cover third-party models, weights, APIs, or tools (such as AudioGen/AudioCraft, SAM 3.1, Muse Spark via the Meta Model API, and ffmpeg), or any game footage or other video content, which remain under their own terms. No model weights are distributed in this repository.

AudioGen weights are licensed for non-commercial use only; MuseFX is a non-commercial hackathon project and uses them on that basis.
