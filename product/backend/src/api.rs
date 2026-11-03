use std::{
    collections::HashSet,
    sync::{Arc, Mutex, OnceLock},
};

use axum::{
    body::Body,
    extract::{multipart::MultipartRejection, DefaultBodyLimit, Multipart, Path, Request, State},
    http::{header, HeaderValue, Method, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, patch, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::json;
use tokio::{fs, io::AsyncWriteExt};
use tower::ServiceExt;
use tower_http::{
    cors::CorsLayer,
    services::{ServeDir, ServeFile},
    trace::TraceLayer,
};
use uuid::Uuid;

use crate::{
    adapters::dependency_available,
    models::{EventsDocument, Job, JobResult, Status},
    pipeline, AppState,
};

pub const MAX_UPLOAD_BYTES: usize = 100 * 1024 * 1024;

pub struct ApiError(StatusCode, String);
impl ApiError {
    fn new(status: StatusCode, message: impl Into<String>) -> Self {
        Self(status, message.into())
    }
    fn internal(error: impl std::fmt::Display) -> Self {
        tracing::error!(%error, "API storage error");
        Self::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not access job storage. Check the backend logs.",
        )
    }
    fn multipart(error: axum::extract::multipart::MultipartError) -> Self {
        Self::new(
            error.status(),
            if error.status() == StatusCode::PAYLOAD_TOO_LARGE {
                "Video must be 100 MB or smaller."
            } else {
                "Invalid multipart upload."
            },
        )
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"detail": self.1}))).into_response()
    }
}

pub fn router(state: AppState, frontend_origin: &str) -> anyhow::Result<Router> {
    let origin: HeaderValue = frontend_origin.parse()?;
    let media = ServeDir::new(state.store.root());
    Ok(Router::new()
        .route("/health", get(health))
        .route("/jobs", post(submit))
        .route("/jobs/{job_id}", get(status))
        .route("/jobs/{job_id}/result", get(result))
        .route("/jobs/{job_id}/events", patch(retime))
        .route("/jobs/{job_id}/download/{artifact}", get(download))
        .nest_service("/media", media)
        .fallback(|| async { ApiError::new(StatusCode::NOT_FOUND, "Route not found") })
        .layer(DefaultBodyLimit::max(MAX_UPLOAD_BYTES + 1024 * 1024))
        .layer(
            CorsLayer::new()
                .allow_origin(origin)
                .allow_methods([Method::GET, Method::POST, Method::PATCH])
                .allow_headers([header::CONTENT_TYPE, header::RANGE]),
        )
        .layer(TraceLayer::new_for_http())
        .with_state(state))
}

async fn health() -> Json<serde_json::Value> {
    let (ffmpeg, ffprobe) = tokio::join!(
        dependency_available("ffmpeg"),
        dependency_available("ffprobe")
    );
    Json(
        json!({"status": if ffmpeg && ffprobe { "ok" } else { "degraded" },
        "dependencies": {"ffmpeg": ffmpeg, "ffprobe": ffprobe}}),
    )
}

async fn submit(
    State(state): State<AppState>,
    multipart: Result<Multipart, MultipartRejection>,
) -> Result<(StatusCode, Json<Job>), ApiError> {
    let mut multipart = multipart
        .map_err(|_| ApiError::new(StatusCode::BAD_REQUEST, "Expected multipart field 'video'."))?;
    let job = Job::new();
    let folder = state.store.folder(job.job_id);
    fs::create_dir(&folder).await.map_err(ApiError::internal)?;
    // Only expose/queue a job after the entire upload is validated and closed.
    let upload = async {
        let mut found = false;
        while let Some(mut field) = multipart.next_field().await.map_err(ApiError::multipart)? {
            if field.name() != Some("video") {
                continue;
            }
            if found {
                return Err(ApiError::new(
                    StatusCode::BAD_REQUEST,
                    "Upload exactly one video.",
                ));
            }
            found = true;
            if !field
                .file_name()
                .is_some_and(|name| name.to_lowercase().ends_with(".mp4"))
            {
                return Err(ApiError::new(
                    StatusCode::UNSUPPORTED_MEDIA_TYPE,
                    "Please upload an MP4 video.",
                ));
            }
            let mut target = fs::File::create(folder.join("original.mp4"))
                .await
                .map_err(ApiError::internal)?;
            let mut size = 0;
            while let Some(chunk) = field.chunk().await.map_err(ApiError::multipart)? {
                size += chunk.len();
                if size > MAX_UPLOAD_BYTES {
                    return Err(ApiError::new(
                        StatusCode::PAYLOAD_TOO_LARGE,
                        "Video must be 100 MB or smaller.",
                    ));
                }
                target.write_all(&chunk).await.map_err(ApiError::internal)?;
            }
            target.flush().await.map_err(ApiError::internal)?;
            if size == 0 {
                return Err(ApiError::new(
                    StatusCode::BAD_REQUEST,
                    "The uploaded file is empty.",
                ));
            }
        }
        if !found {
            return Err(ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                "Missing multipart field 'video'.",
            ));
        }
        state.store.save(&job).await.map_err(ApiError::internal)
    }
    .await;
    if let Err(error) = upload {
        if let Err(cleanup_error) = fs::remove_dir_all(&folder).await {
            tracing::warn!(%cleanup_error, "Could not remove rejected upload");
        }
        return Err(error);
    }
    tokio::spawn(pipeline::run(state, job.clone()));
    Ok((StatusCode::ACCEPTED, Json(job)))
}

fn parse_id(id: &str) -> Result<Uuid, ApiError> {
    Uuid::parse_str(id).map_err(|_| ApiError::new(StatusCode::NOT_FOUND, "Job not found"))
}

async fn load(state: &AppState, id: &str) -> Result<Job, ApiError> {
    let id = parse_id(id)?;
    state.store.load(id).await.map_err(|error| {
        if error
            .downcast_ref::<std::io::Error>()
            .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound)
        {
            ApiError::new(StatusCode::NOT_FOUND, "Job not found")
        } else {
            ApiError::internal(error)
        }
    })
}

async fn status(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Job>, ApiError> {
    Ok(Json(load(&state, &id).await?))
}

fn require_complete(job: &Job) -> Result<(), ApiError> {
    if job.status != Status::Completed {
        return Err(ApiError::new(
            StatusCode::CONFLICT,
            job.error.as_deref().unwrap_or("Result is not ready yet."),
        ));
    }
    Ok(())
}

async fn build_result(state: &AppState, job: &Job) -> Result<JobResult, ApiError> {
    let bytes = fs::read(state.store.folder(job.job_id).join("events.json"))
        .await
        .map_err(ApiError::internal)?;
    let events: EventsDocument = serde_json::from_slice(&bytes).map_err(ApiError::internal)?;
    events.validate().map_err(ApiError::internal)?;
    let prefix = format!("/media/{}", job.job_id);
    Ok(JobResult {
        original_video_url: format!("{prefix}/original.mp4"),
        result_video_url: format!("{prefix}/result.mp4"),
        events_url: format!("{prefix}/events.json"),
        events,
    })
}

async fn result(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<JobResult>, ApiError> {
    let job = load(&state, &id).await?;
    require_complete(&job)?;
    Ok(Json(build_result(&state, &job).await?))
}

#[derive(Deserialize)]
struct RetimeRequest {
    events: Vec<Retime>,
}

#[derive(Deserialize)]
struct Retime {
    id: String,
    t: f64,
}

/// Jobs whose sound is being re-mixed right now, so edits cannot overlap.
fn retiming() -> &'static Mutex<HashSet<Uuid>> {
    static SET: OnceLock<Mutex<HashSet<Uuid>>> = OnceLock::new();
    SET.get_or_init(Default::default)
}

struct RetimeGuard(Uuid);
impl Drop for RetimeGuard {
    fn drop(&mut self) {
        if let Ok(mut set) = retiming().lock() {
            set.remove(&self.0);
        }
    }
}

/// Moves events in time and re-mixes the video. Sounds are reused from the Audio
/// cache, so no new generation happens. Only `t` can change; everything else in
/// events.json stays as Detection wrote it.
async fn retime(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<RetimeRequest>,
) -> Result<Json<JobResult>, ApiError> {
    let job = load(&state, &id).await?;
    require_complete(&job)?;
    let _guard = {
        let mut set = retiming().lock().map_err(ApiError::internal)?;
        if !set.insert(job.job_id) {
            return Err(ApiError::new(
                StatusCode::CONFLICT,
                "Still applying the previous change. Try again in a moment.",
            ));
        }
        RetimeGuard(job.job_id)
    };
    let folder = state.store.folder(job.job_id);
    let mut document: EventsDocument = serde_json::from_slice(
        &fs::read(folder.join("events.json"))
            .await
            .map_err(ApiError::internal)?,
    )
    .map_err(ApiError::internal)?;
    for change in &body.events {
        let event = document
            .events
            .iter_mut()
            .find(|event| event.id == change.id)
            .ok_or_else(|| {
                ApiError::new(
                    StatusCode::UNPROCESSABLE_ENTITY,
                    format!("Unknown event '{}'.", change.id),
                )
            })?;
        event.t = change.t;
    }
    document
        .validate()
        .map_err(|error| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, error.to_string()))?;

    // Render beside the current result, then swap it in, so a failure keeps
    // both the old video and the old events.
    let staged = folder.join("result.edit.mp4");
    state
        .audio
        .render(&folder.join("original.mp4"), &document, &staged)
        .await
        .map_err(|error| {
            tracing::warn!(error = ?error, "Re-mix failed");
            ApiError::new(StatusCode::BAD_GATEWAY, error.to_string())
        })?;
    let produced = fs::metadata(&staged).await.map_err(ApiError::internal)?;
    if !produced.is_file() || produced.len() == 0 {
        return Err(ApiError::new(
            StatusCode::BAD_GATEWAY,
            "Audio did not produce a result video.",
        ));
    }
    fs::write(
        folder.join("events.tmp"),
        serde_json::to_vec_pretty(&document).map_err(ApiError::internal)?,
    )
    .await
    .map_err(ApiError::internal)?;
    fs::rename(&staged, folder.join("result.mp4"))
        .await
        .map_err(ApiError::internal)?;
    fs::rename(folder.join("events.tmp"), folder.join("events.json"))
        .await
        .map_err(ApiError::internal)?;
    Ok(Json(build_result(&state, &job).await?))
}

async fn download(
    State(state): State<AppState>,
    Path((id, artifact)): Path<(String, String)>,
    request: Request,
) -> Result<Response, ApiError> {
    if !matches!(artifact.as_str(), "result.mp4" | "events.json") {
        return Err(ApiError::new(StatusCode::NOT_FOUND, "Artifact not found"));
    }
    let job = load(&state, &id).await?;
    require_complete(&job)?;
    let response = ServeFile::new(state.store.folder(job.job_id).join(&artifact))
        .oneshot(request)
        .await;
    let mut response = match response {
        Ok(response) => response.map(Body::new),
        Err(error) => match error {},
    };
    response.headers_mut().insert(
        header::CONTENT_DISPOSITION,
        HeaderValue::from_str(&format!("attachment; filename=\"{artifact}\""))
            .map_err(ApiError::internal)?,
    );
    Ok(response)
}

/// Convenience constructor for model-free local demos; inject traits for real adapters.
pub async fn mock_state(root: std::path::PathBuf) -> anyhow::Result<AppState> {
    Ok(AppState {
        store: crate::store::JobStore::new(root).await?,
        detection: Arc::new(crate::adapters::MockDetection),
        audio: Arc::new(crate::adapters::MockAudio),
    })
}
