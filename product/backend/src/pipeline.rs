use anyhow::{ensure, Context, Result};
use tokio::fs;

use crate::{
    adapters::{compose_prompt, ReviewVerdict},
    models::{EventsDocument, Job, Stage, Status},
    AppState,
};

/// Apply review verdicts. Returns Some(adjusted) only if something changed.
fn apply_verdicts(events: &EventsDocument, verdicts: &[ReviewVerdict]) -> Option<EventsDocument> {
    let mut adjusted = events.clone();
    let mut changed = false;
    for verdict in verdicts {
        let Some(event) = adjusted.events.iter_mut().find(|e| e.id == verdict.id) else {
            continue;
        };
        if verdict.action == "regenerate" && verdict.match_score < 0.6 {
            if let Some(slots) = &verdict.slots {
                let type_name = serde_json::to_value(event.event_type)
                    .ok()
                    .and_then(|v| v.as_str().map(str::to_owned))
                    .unwrap_or_else(|| "other".into());
                let new_prompt = compose_prompt(slots, &type_name, &event.prompt);
                if new_prompt != event.prompt {
                    event.prompt = new_prompt;
                    changed = true;
                }
            }
        }
        if (verdict.action == "shift" || verdict.action == "regenerate")
            && verdict.timing_shift_ms.abs() >= 30.0
            && verdict.timing_shift_ms.abs() <= 500.0
        {
            let new_t = (event.t + verdict.timing_shift_ms / 1000.0)
                .clamp(0.0, (adjusted.video.duration - event.duration).max(0.0));
            if (new_t - event.t).abs() > 1e-9 {
                event.t = new_t;
                changed = true;
            }
        }
    }
    changed.then_some(adjusted)
}

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
    // Second pass (one iteration): Muse review -> shift timing and/or
    // regenerate mismatched sounds with a more specific prompt, then re-render.
    // Shift-only fixes reuse cached sounds; a changed prompt regenerates just
    // that event. Review failure never fails the job.
    if let Some(review) = &state.review {
        match review.review(&output, &events).await {
            Ok(verdicts) => {
                if let Some(adjusted) = apply_verdicts(&events, &verdicts) {
                    if adjusted.validate().is_ok() {
                        let _ = fs::write(
                            folder.join("events.json"),
                            serde_json::to_vec_pretty(&adjusted)?,
                        )
                        .await;
                        state.audio.render(&source, &adjusted, &output).await?;
                        let metadata = fs::metadata(&output).await.context(
                            "Audio adapter did not produce a result video after review.",
                        )?;
                        ensure!(
                            metadata.is_file() && metadata.len() > 0,
                            "Audio adapter did not produce a result video after review."
                        );
                    }
                }
            }
            Err(error) => tracing::warn!(%error, "Second-pass review failed; keeping first render"),
        }
    }
    job.status = Status::Completed;
    job.stage = Stage::Done;
    job.progress = 100;
    Ok(())
}
