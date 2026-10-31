use std::collections::HashSet;

use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Video {
    pub id: String,
    pub duration: f64,
    pub fps: f64,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EventType {
    Footstep,
    Impact,
    Hit,
    Door,
    Pickup,
    Ui,
    Whoosh,
    Other,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Event {
    pub id: String,
    pub t: f64,
    pub duration: f64,
    #[serde(rename = "type")]
    pub event_type: EventType,
    pub prompt: String,
    pub volume: f64,
    pub pan: f64,
    pub confidence: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Ambience {
    pub prompt: String,
    pub volume: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EventsDocument {
    pub video: Video,
    pub events: Vec<Event>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ambience: Option<Ambience>,
}

impl EventsDocument {
    /// Semantic validation for the frozen team contract in /Agents.md.
    pub fn validate(&self) -> Result<()> {
        ensure!(!self.video.id.is_empty(), "Video ID must be nonempty.");
        ensure!(
            self.video.duration.is_finite() && self.video.duration > 0.0,
            "Video duration must be finite and positive."
        );
        ensure!(
            self.video.fps.is_finite() && self.video.fps > 0.0,
            "Video FPS must be finite and positive."
        );
        let mut ids = HashSet::new();
        for event in &self.events {
            ensure!(
                !event.id.is_empty() && ids.insert(&event.id),
                "Event IDs must be unique and nonempty."
            );
            ensure!(!event.prompt.is_empty(), "Event prompts must be nonempty.");
            ensure!(
                event.t.is_finite() && event.t >= 0.0,
                "Event timestamps must be finite and nonnegative."
            );
            ensure!(
                event.duration.is_finite() && event.duration > 0.0,
                "Event durations must be finite and positive."
            );
            ensure!(
                event.confidence.is_finite() && (0.0..=1.0).contains(&event.confidence),
                "Event confidence must be between zero and one."
            );
            ensure!(
                event.volume.is_finite() && (0.0..=1.0).contains(&event.volume),
                "Event volume must be between zero and one."
            );
            ensure!(
                event.pan.is_finite() && (-1.0..=1.0).contains(&event.pan),
                "Event pan must be between minus one and one."
            );
            ensure!(
                event.t + event.duration <= self.video.duration + 1e-6,
                "Events must start and finish within the video duration."
            );
        }
        if let Some(ambience) = &self.ambience {
            ensure!(
                !ambience.prompt.is_empty(),
                "Ambience prompt must be nonempty."
            );
            ensure!(
                ambience.volume.is_finite() && (0.0..=1.0).contains(&ambience.volume),
                "Ambience volume must be between zero and one."
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Queued,
    Running,
    Completed,
    Failed,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Detecting,
    Describing,
    Generating,
    Mixing,
    Done,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Job {
    pub job_id: uuid::Uuid,
    pub status: Status,
    pub stage: Stage,
    pub progress: u8,
    pub error: Option<String>,
}

impl Job {
    pub fn new() -> Self {
        Self {
            job_id: uuid::Uuid::new_v4(),
            status: Status::Queued,
            stage: Stage::Detecting,
            progress: 0,
            error: None,
        }
    }
}

impl Default for Job {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Serialize, Deserialize)]
pub struct JobResult {
    pub original_video_url: String,
    pub result_video_url: String,
    pub events_url: String,
    pub events: EventsDocument,
}
