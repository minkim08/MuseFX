# Product conventions

The user requested Rust for all backend work. Use Next.js + TypeScript in `frontend/`, Rust + Axum/Tokio in `backend/`. Keep the mock adapters runnable without model credentials. Do not add a Python backend or integrate real models in this iteration.

## Shared contracts

Read [the team contract](../Agents.md). The original standalone Product prototype used a different schema; the repository implementation must use the team's frozen schema:
- `video`: `id`, `duration`, `fps`.
- `events`: `id`, `t`, `duration`, `type`, `prompt`, `volume`, `pan`, `confidence`.
- Optional `ambience`: `prompt`, `volume`.
- API stages: `detecting`, `describing`, `generating`, `mixing`, `done`, `failed`.

Keep the schema stable and pass Detection's document through unchanged. The Rust stack replaces the earlier FastAPI plan at the user's instruction; this does not authorize changing Detection/Audio implementations or the shared data contract.

## Backend

- Async `DetectionAdapter` and `AudioAdapter` traits live in `backend/src/adapters.rs`; inject `Arc<dyn ...>` through `AppState`.
- Detection writes and returns the same validated `EventsDocument`. Audio writes a nonempty MP4 preserving source pictures/audio and timing.
- Models/semantic validation: `models.rs`; orchestration: `pipeline.rs`; local atomic metadata/recovery: `store.rs`; HTTP/upload/media handling: `api.rs`.
- Use Tokio I/O and subprocesses; put CPU-bound inference in `spawn_blocking`. Never interpolate filenames into shell commands. Check exit codes, time out external programs, and terminate timed-out children.
- Jobs live under `backend/data/jobs/<uuid>/`; never commit uploads, generated media, credentials, or `target/`. Commit Cargo.lock when generated.
- Use one process. Status/progress must be monotonic. Fail interrupted jobs on restart with actionable errors.

## Validation

From `backend/`: `cargo fmt --check`, `cargo test`, `cargo clippy --all-targets -- -D warnings`. With FFmpeg/ffprobe: `cargo test --test api -- --ignored`. From `frontend/`: `npm run typecheck` and `npm run build`. Do not report unrun checks as passing.
