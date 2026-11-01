use anyhow::Result;
use std::{env, path::PathBuf};
use video_to_sfx::api::{mock_state, router};

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "video_to_sfx=info,tower_http=info".into()),
        )
        .init();
    let root = env::var_os("DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("data/jobs"));
    let mut state = mock_state(root).await?;
    if let Ok(url) = env::var("DETECTION_API_URL") {
        if !url.trim().is_empty() {
            tracing::info!(%url, "Using real Detection service");
            state.detection = std::sync::Arc::new(video_to_sfx::adapters::HttpDetection::new(url));
        }
    }
    if let Ok(dir) = env::var("AUDIO_DIR") {
        if !dir.trim().is_empty() {
            let fake = env::var("AUDIO_FAKE").is_ok_and(|value| value == "1");
            tracing::info!(%dir, fake, "Using real Audio component");
            state.audio = std::sync::Arc::new(video_to_sfx::adapters::PythonAudio::from_dir(
                std::path::Path::new(&dir),
                fake,
            )?);
        }
    }
    state.store.recover_interrupted().await?;
    let origin = env::var("FRONTEND_ORIGIN").unwrap_or_else(|_| "http://localhost:3000".into());
    let address = env::var("BIND_ADDR").unwrap_or_else(|_| "127.0.0.1:8000".into());
    let listener = tokio::net::TcpListener::bind(&address).await?;
    tracing::info!(%address, "Video to SFX backend listening");
    axum::serve(listener, router(state, &origin)?)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
