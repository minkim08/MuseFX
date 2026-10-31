use anyhow::{ensure, Context, Result};
use tokio::fs;

use crate::{
    models::{EventsDocument, Job, Stage, Status},
    AppState,
};

pub async fn run(state: AppState, mut job: Job) {
    if let Err(error) = process(&state, &mut job).await {
        tracing::error!(job_id = %job.job_id, error = ?error, "Pipeline failed");
        job.status = Status::Failed;
        job.stage = Stage::Failed;
        job.error = Some(error.to_string());
    }
    if let Err(error) = state.store.save(&job).await {
        tracing::error!(job_id = %job.job_id, %error, "Could not save final job status");
    }
}

async fn process(state: &AppState, job: &mut Job) -> Result<()> {
    let folder = state.store.folder(job.job_id);
    let source = folder.join("original.mp4");
    job.status = Status::Running;
    job.stage = Stage::Detecting;
    job.progress = 15;
    state.store.save(job).await?;
    let events = state
        .detection
        .detect(&source, &folder.join("events.json"))
        .await?;
    events.validate()?;
    job.stage = Stage::Describing;
    job.progress = 40;
    state.store.save(job).await?;
    let written: EventsDocument = serde_json::from_slice(
        &fs::read(folder.join("events.json"))
            .await
            .context("Detection adapter did not write events.json.")?,
    )?;
    written.validate()?;
    ensure!(
        written == events,
        "Detection adapter returned events that differ from events.json."
    );
    job.stage = Stage::Generating;
    job.progress = 55;
    state.store.save(job).await?;
    let output = folder.join("result.mp4");
    job.stage = Stage::Mixing;
    job.progress = 75;
    state.store.save(job).await?;
    state.audio.render(&source, &events, &output).await?;
    let metadata = fs::metadata(&output)
        .await
        .context("Audio adapter did not produce a result video.")?;
    ensure!(
        metadata.is_file() && metadata.len() > 0,
        "Audio adapter did not produce a result video."
    );
    job.status = Status::Completed;
    job.stage = Stage::Done;
    job.progress = 100;
    Ok(())
}
