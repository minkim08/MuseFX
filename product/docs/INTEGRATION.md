# Detection and Audio handoff

The backend uses `MockAudio` unless `AUDIO_DIR` is set, which enables `PythonAudio`: it runs the component in [`audio/`](../../audio/README.md) as a subprocess, with its own 60-minute limit because a GPU cold start can exceed `run_media`'s 180 seconds. Real Detection is implemented as the Detection API service in [`detection/`](../../detection/README.md); the backend's `HttpDetection` adapter calls it when `DETECTION_API_URL` is set (mock otherwise). The shared [`events.json` contract](../../Agents.md#interface-contract-eventsjson) is authoritative; see the [example](events.example.json).

## Detection

Implement `DetectionAdapter::detect(&self, video: &Path, output: &Path) -> anyhow::Result<EventsDocument>` with `#[async_trait]`. Build a document using `video.id/duration/fps` and events with `id/t/duration/type/prompt/volume/pan/confidence`. Call `.validate()`, write `serde_json::to_vec_pretty(&document)?` using Tokio I/O, and return the same document.

All times are seconds from video start. IDs are unique and nonempty, prompts nonempty, numbers finite, confidence/volume within [0,1], pan within [-1,1], and positive event durations contained in the clip. Empty event lists and omitted ambience are valid. Serde checks shape; `.validate()` checks semantics. Enum `EventType` serializes to the team's lowercase `type` values.

## Audio

Implement `AudioAdapter::render(&self, video: &Path, events: &EventsDocument, output: &Path) -> anyhow::Result<()>` with `#[async_trait]`. Preserve source pictures/audio and clip duration, place each sound's attack at `t`, apply volume/pan, skip confidence below 0.5, and handle optional ambience. Write a nonempty playable MP4.

Mock Audio generates 880 Hz event test tones only, using event volume/pan; it passes ambience through to the result JSON but does not synthesize an ambience bed. Full generation/mixing remains owned by Component 2.

Return actionable errors. Use `run_media` for checked subprocesses with a 180-second timeout and child termination on timeout. Use `spawn_blocking` for CPU-bound inference. Traits require `Send + Sync`, since separate jobs can invoke adapters concurrently.

## Wiring

Construct state in `backend/src/main.rs` and replace one adapter independently:

```rust
let mut state = mock_state(root).await?;
state.detection = std::sync::Arc::new(MyDetection);
state.store.recover_interrupted().await?;
```

Replace `state.audio` similarly. For Python-based team models, a future Rust adapter can call the team's service; Product's Rust backend does not require rewriting the Detection/Audio team's model code. Model-service transport/deployment is not implemented yet. Do not change HTTP routes, event schema, or UI when swapping adapters.

## Demo checklist

1. Start `cargo run` in `product/backend`; check `/health` for FFmpeg/ffprobe.
2. Run Rust tests/Clippy and frontend typecheck/build from the Product README.
3. Generate a sample with `product/scripts/make_sample.sh` and upload it.
4. Confirm unchanged source video frames and a short tone at one second in the result.
5. Seek using an event marker and row. Download/open MP4 and events independently.
6. Try invalid files and corrupt MP4s; confirm readable errors and retries.
7. Interrupt a processing job and restart the backend; verify it becomes failed.

One backend process only. Local files are unauthenticated. Interrupted work does not resume; reupload to retry. Hosting and durable queues are future work.
