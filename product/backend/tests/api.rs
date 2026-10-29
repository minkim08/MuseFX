use std::{path::Path, sync::Arc, time::Duration};

use anyhow::{bail, Result};
use async_trait::async_trait;
use axum::{body::{to_bytes, Body}, http::{Request, StatusCode}, response::Response, Router};
use serde_json::Value;
use tempfile::TempDir;
use tokio::{fs, time::{sleep, timeout}};
use tower::ServiceExt;
use video_to_sfx::{
    adapters::{AudioAdapter, DetectionAdapter},
    api::{mock_state, router},
    models::{EventsDocument, Job, Stage, Status, Video},
    pipeline,
    store::JobStore,
    AppState,
};

struct StubDetection;
#[async_trait]
impl DetectionAdapter for StubDetection {
    async fn detect(&self, _: &Path, output: &Path) -> Result<EventsDocument> {
        let events = EventsDocument { video: Video { id: "test".into(), duration: 2.0, fps: 24.0 }, events: vec![], ambience: None };
        fs::write(output, serde_json::to_vec(&events)?).await?;
        Ok(events)
    }
}
struct FailingAudio;
#[async_trait]
impl AudioAdapter for FailingAudio {
    async fn render(&self, _: &Path, _: &EventsDocument, _: &Path) -> Result<()> {
        bail!("Audio adapter unavailable")
    }
}
struct MissingAudio;
#[async_trait]
impl AudioAdapter for MissingAudio {
    async fn render(&self, _: &Path, _: &EventsDocument, _: &Path) -> Result<()> { Ok(()) }
}

async fn setup(audio: Arc<dyn AudioAdapter>) -> (TempDir, AppState, Router) {
    let dir = tempfile::tempdir().unwrap();
    let state = AppState { store: JobStore::new(dir.path().to_owned()).await.unwrap(),
        detection: Arc::new(StubDetection), audio };
    let app = router(state.clone(), "http://localhost:3000").unwrap();
    (dir, state, app)
}

fn upload(filename: &str, data: &[u8]) -> Request<Body> {
    let mut body = format!("--test-boundary\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{filename}\"\r\nContent-Type: video/mp4\r\n\r\n").into_bytes();
    body.extend_from_slice(data);
    body.extend_from_slice(b"\r\n--test-boundary--\r\n");
    Request::post("/jobs").header("content-type", "multipart/form-data; boundary=test-boundary")
        .body(Body::from(body)).unwrap()
}
async fn json(response: Response) -> Value {
    serde_json::from_slice(&to_bytes(response.into_body(), 1024 * 1024).await.unwrap()).unwrap()
}
async fn get(app: &Router, path: &str) -> Response {
    app.clone().oneshot(Request::get(path).body(Body::empty()).unwrap()).await.unwrap()
}
async fn finish(state: &AppState, id: uuid::Uuid) -> Job {
    timeout(Duration::from_secs(30), async {
        loop {
            let job = state.store.load(id).await.unwrap();
            if matches!(job.status, Status::Failed | Status::Completed) { return job; }
            sleep(Duration::from_millis(25)).await;
        }
    }).await.expect("job did not finish in time")
}

#[tokio::test]
async fn rejects_invalid_uploads_and_cleans_storage() {
    let (dir, _, app) = setup(Arc::new(FailingAudio)).await;
    for (name, bytes, status) in [
        ("clip.txt", b"bad".as_slice(), StatusCode::UNSUPPORTED_MEDIA_TYPE),
        ("clip.mp4", b"".as_slice(), StatusCode::BAD_REQUEST),
    ] {
        let response = app.clone().oneshot(upload(name, bytes)).await.unwrap();
        assert_eq!(response.status(), status);
        assert!(json(response).await["detail"].is_string());
    }
    assert!(fs::read_dir(dir.path()).await.unwrap().next_entry().await.unwrap().is_none());
    assert_eq!(get(&app, "/jobs/not-a-job").await.status(), StatusCode::NOT_FOUND);
    assert_eq!(get(&app, &format!("/jobs/{}", uuid::Uuid::new_v4())).await.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn malformed_multipart_returns_json_error() {
    let (_dir, _state, app) = setup(Arc::new(FailingAudio)).await;
    let response = app.oneshot(Request::post("/jobs").body(Body::from("bad")).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(json(response).await["detail"].is_string());
}

#[tokio::test]
async fn adapter_failure_is_visible_and_result_is_unavailable() {
    let (_dir, state, app) = setup(Arc::new(FailingAudio)).await;
    let response = app.clone().oneshot(upload("clip.mp4", b"stub")).await.unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let queued: Job = serde_json::from_value(json(response).await).unwrap();
    assert_eq!(queued.status, Status::Queued);
    let job = finish(&state, queued.job_id).await;
    assert_eq!(job.status, Status::Failed);
    assert_eq!(job.progress, 75);
    assert_eq!(job.error.as_deref(), Some("Audio adapter unavailable"));
    let id = job.job_id;
    assert_eq!(get(&app, &format!("/jobs/{id}/result")).await.status(), StatusCode::CONFLICT);
    assert_eq!(get(&app, &format!("/jobs/{id}/download/result.mp4")).await.status(), StatusCode::CONFLICT);
}

#[tokio::test]
async fn missing_audio_output_fails_instead_of_completing() {
    let (_dir, state, app) = setup(Arc::new(MissingAudio)).await;
    let queued: Job = serde_json::from_value(json(app.oneshot(upload("clip.mp4", b"stub")).await.unwrap()).await).unwrap();
    let job = finish(&state, queued.job_id).await;
    assert_eq!(job.status, Status::Failed);
    assert!(job.error.unwrap().contains("did not produce"));
}

#[tokio::test]
async fn detection_return_value_must_match_file() {
    struct InconsistentDetection;
    #[async_trait]
    impl DetectionAdapter for InconsistentDetection {
        async fn detect(&self, _: &Path, output: &Path) -> Result<EventsDocument> {
            let mut events = EventsDocument { video: Video { id: "test".into(), duration: 3.0, fps: 24.0 }, events: vec![], ambience: None };
            fs::write(output, serde_json::to_vec(&events)?).await?;
            events.video.duration = 2.0;
            Ok(events)
        }
    }
    let (_dir, mut state, _) = setup(Arc::new(MissingAudio)).await;
    state.detection = Arc::new(InconsistentDetection);
    let job = Job::new();
    fs::create_dir(state.store.folder(job.job_id)).await.unwrap();
    pipeline::run(state.clone(), job.clone()).await;
    let failed = state.store.load(job.job_id).await.unwrap();
    assert_eq!(failed.progress, 40);
    assert!(failed.error.unwrap().contains("differ from events.json"));
}

#[tokio::test]
async fn restart_fails_interrupted_jobs_and_preserves_finished_jobs() {
    let (_dir, state, _) = setup(Arc::new(MissingAudio)).await;
    let mut interrupted = Job::new();
    interrupted.status = Status::Running;
    interrupted.stage = Stage::Mixing;
    interrupted.progress = 55;
    let mut completed = Job::new();
    completed.status = Status::Completed;
    completed.stage = Stage::Done;
    completed.progress = 100;
    for job in [&interrupted, &completed] {
        fs::create_dir(state.store.folder(job.job_id)).await.unwrap();
        state.store.save(job).await.unwrap();
    }
    state.store.recover_interrupted().await.unwrap();
    let recovered = state.store.load(interrupted.job_id).await.unwrap();
    assert_eq!(recovered.status, Status::Failed);
    assert_eq!(recovered.progress, 55);
    assert!(recovered.error.unwrap().contains("server restart"));
    assert_eq!(state.store.load(completed.job_id).await.unwrap().status, Status::Completed);
}

#[tokio::test]
async fn health_and_cors_remain_compatible() {
    let (_dir, _, app) = setup(Arc::new(MissingAudio)).await;
    let response = app.oneshot(Request::get("/health").header("origin", "http://localhost:3000")
        .body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["access-control-allow-origin"], "http://localhost:3000");
    let document = json(response).await;
    assert!(document["dependencies"]["ffmpeg"].is_boolean());
    assert!(document["dependencies"]["ffprobe"].is_boolean());
}

// Explicitly run with `cargo test --test api -- --ignored` on a host with FFmpeg.
#[tokio::test]
#[ignore = "requires ffmpeg and ffprobe"]
async fn real_mp4_preserves_frames_and_adds_audio() {
    use video_to_sfx::adapters::{dependency_available, probe, run_media};
    use tokio::process::Command;
    assert!(dependency_available("ffmpeg").await && dependency_available("ffprobe").await);
    for original_audio in [false, true] {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("sample.mp4");
        let mut command = Command::new("ffmpeg");
        command.args(["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=2"]);
        if original_audio {
            command.args(["-f", "lavfi", "-i", "sine=frequency=220:duration=2"]);
        }
        run_media(command.args(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]).arg(&source)).await.unwrap();
        let state = mock_state(dir.path().join("jobs")).await.unwrap();
        let app = router(state.clone(), "http://localhost:3000").unwrap();
        let bytes = fs::read(&source).await.unwrap();
        let queued: Job = serde_json::from_value(json(app.clone().oneshot(upload("sample.mp4", &bytes)).await.unwrap()).await).unwrap();
        let job = finish(&state, queued.job_id).await;
        assert_eq!(job.status, Status::Completed, "{:?}", job.error);
        assert_eq!(job.progress, 100);
        let id = job.job_id;
        let result = json(get(&app, &format!("/jobs/{id}/result")).await).await;
        assert!((result["events"]["events"][0]["t"].as_f64().unwrap() - 2.0 / 3.0).abs() < 1e-6);
        assert_eq!(json(get(&app, result["events_url"].as_str().unwrap()).await).await, result["events"]);
        let original = get(&app, result["original_video_url"].as_str().unwrap()).await;
        assert_eq!(&to_bytes(original.into_body(), 1024 * 1024).await.unwrap()[..], &bytes);
        let attachment = get(&app, &format!("/jobs/{id}/download/result.mp4")).await;
        assert_eq!(attachment.status(), StatusCode::OK);
        assert!(attachment.headers()["content-disposition"].to_str().unwrap().contains("attachment;"));
        assert_eq!(get(&app, &format!("/jobs/{id}/download/job.json")).await.status(), StatusCode::NOT_FOUND);
        let range = app.clone().oneshot(Request::get(result["result_video_url"].as_str().unwrap())
            .header("range", "bytes=0-99").body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(range.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(to_bytes(range.into_body(), 1024).await.unwrap().len(), 100);
        let output = state.store.folder(id).join("result.mp4");
        let metadata = probe(&output).await.unwrap();
        assert!(metadata.streams.iter().any(|s| s.codec_type == "audio"));
        assert!((metadata.format.duration.unwrap().parse::<f64>().unwrap() - 2.0).abs() < 0.1);
        let mut hashes = vec![];
        for path in [&source, &output] {
            hashes.push(run_media(Command::new("ffmpeg").args(["-v", "error", "-i"]).arg(path)
                .args(["-map", "0:v:0", "-f", "framemd5", "-"])).await.unwrap());
        }
        assert_eq!(hashes[0], hashes[1], "source video frames changed");
        if !original_audio {
            let pcm = run_media(Command::new("ffmpeg").args(["-v", "error", "-i"]).arg(&output)
                .args(["-ss", "0.7", "-t", "0.1", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"])).await.unwrap();
            assert!(pcm.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]).unsigned_abs()).max().unwrap() > 1000);
        }
    }
}

#[tokio::test]
async fn successful_adapter_job_returns_compatible_results_and_downloads() {
    struct StubAudio;
    #[async_trait]
    impl AudioAdapter for StubAudio {
        async fn render(&self, video: &Path, _: &EventsDocument, output: &Path) -> Result<()> {
            // API fixture only; real playable media is covered by the FFmpeg test.
            fs::copy(video, output).await?;
            Ok(())
        }
    }
    let (_dir, state, app) = setup(Arc::new(StubAudio)).await;
    let response = app.clone().oneshot(upload("clip.mp4", b"adapter fixture")).await.unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let queued: Job = serde_json::from_value(json(response).await).unwrap();
    let job = finish(&state, queued.job_id).await;
    assert_eq!(job.status, Status::Completed);
    assert_eq!(job.progress, 100);
    let id = job.job_id;
    let status = json(get(&app, &format!("/jobs/{id}")).await).await;
    assert_eq!(status["stage"], "done");
    assert!(status["error"].is_null());
    let result = json(get(&app, &format!("/jobs/{id}/result")).await).await;
    assert_eq!(result["original_video_url"], format!("/media/{id}/original.mp4"));
    assert_eq!(result["events"]["video"]["fps"], 24.0);
    assert_eq!(json(get(&app, result["events_url"].as_str().unwrap()).await).await, result["events"]);
    let download = get(&app, &format!("/jobs/{id}/download/result.mp4")).await;
    assert_eq!(download.status(), StatusCode::OK);
    assert_eq!(download.headers()["content-disposition"], "attachment; filename=\"result.mp4\"");
    assert_eq!(&to_bytes(download.into_body(), 1024).await.unwrap()[..], b"adapter fixture");
    let events_download = get(&app, &format!("/jobs/{id}/download/events.json")).await;
    assert!(events_download.headers()["content-disposition"].to_str().unwrap().contains("events.json"));
    assert_eq!(get(&app, &format!("/jobs/{id}/download/job.json")).await.status(), StatusCode::NOT_FOUND);
}
