use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use tokio::fs;
use uuid::Uuid;

use crate::models::{Job, Stage, Status};

#[derive(Clone)]
pub struct JobStore { root: PathBuf }

impl JobStore {
    pub async fn new(root: PathBuf) -> Result<Self> {
        fs::create_dir_all(&root).await.context("Could not create job storage.")?;
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path { &self.root }
    pub fn folder(&self, id: Uuid) -> PathBuf { self.root.join(id.to_string()) }

    pub async fn load(&self, id: Uuid) -> Result<Job> {
        Ok(serde_json::from_slice(&fs::read(self.folder(id).join("job.json")).await?)?)
    }

    pub async fn save(&self, job: &Job) -> Result<()> {
        let folder = self.folder(job.job_id);
        fs::write(folder.join("job.tmp"), serde_json::to_vec_pretty(job)?).await?;
        fs::rename(folder.join("job.tmp"), folder.join("job.json")).await?;
        Ok(())
    }

    /// A local background task cannot survive a process restart.
    pub async fn recover_interrupted(&self) -> Result<()> {
        let mut entries = fs::read_dir(&self.root).await?;
        while let Some(entry) = entries.next_entry().await? {
            let Ok(id) = Uuid::parse_str(&entry.file_name().to_string_lossy()) else { continue };
            match self.load(id).await {
                Ok(mut job) if matches!(job.status, Status::Queued | Status::Running) => {
                    job.status = Status::Failed;
                    job.stage = Stage::Failed;
                    job.error = Some("Processing was interrupted by a server restart. Please upload the video again.".into());
                    self.save(&job).await?;
                }
                Err(error) => tracing::warn!(%error, %id, "Could not recover job metadata"),
                _ => {}
            }
        }
        Ok(())
    }
}
