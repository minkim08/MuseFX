use std::{
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use anyhow::{anyhow, bail, ensure, Context, Result};
use async_trait::async_trait;
use serde::Deserialize;
use tokio::{fs, process::Command, time::timeout};

use crate::models::{Event, EventType, EventsDocument, Video};

#[async_trait]
pub trait DetectionAdapter: Send + Sync {
    async fn detect(&self, video: &Path, output: &Path) -> Result<EventsDocument>;
}

#[async_trait]
pub trait AudioAdapter: Send + Sync {
    async fn render(&self, video: &Path, events: &EventsDocument, output: &Path) -> Result<()>;
}

/// Argument lists avoid shell interpolation; a dropped timed-out child is killed.
pub async fn run_media(command: &mut Command) -> Result<Vec<u8>> {
    command.kill_on_drop(true).stdin(Stdio::null());
    let output = timeout(Duration::from_secs(180), command.output())
        .await
        .context("Video processing exceeded the 180 second limit.")?
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                anyhow!("FFmpeg and ffprobe must be installed and on PATH.")
            } else {
                anyhow!(error).context("Could not start video processing.")
            }
        })?;
    if !output.status.success() {
        tracing::warn!(stderr = %String::from_utf8_lossy(&output.stderr), "Media command failed");
        bail!("Could not process this video. Please upload a valid MP4.");
    }
    Ok(output.stdout)
}

#[derive(Debug, Deserialize)]
pub struct ProbeStream {
    pub codec_type: String,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub avg_frame_rate: Option<String>,
    pub r_frame_rate: Option<String>,
}
#[derive(Debug, Deserialize)]
pub struct ProbeFormat {
    pub duration: Option<String>,
}
#[derive(Debug, Deserialize)]
pub struct Probe {
    pub streams: Vec<ProbeStream>,
    pub format: ProbeFormat,
}

pub async fn probe(video: &Path) -> Result<Probe> {
    let bytes = run_media(
        Command::new("ffprobe")
            .args([
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
            ])
            .arg(video),
    )
    .await?;
    serde_json::from_slice(&bytes).context("Could not read video metadata.")
}

pub async fn dependency_available(name: &str) -> bool {
    matches!(timeout(Duration::from_secs(3), Command::new(name).arg("-version")
        .kill_on_drop(true).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status())
        .await, Ok(Ok(status)) if status.success())
}

/// Real Detection: HTTP client for the Detection service (detection/service.py).
/// Submits the clip, polls until done, writes events.json, returns it parsed —
/// so pipeline validation and file-equality checks pass unchanged.
pub struct HttpDetection {
    base_url: String,
    client: reqwest::Client,
}

impl HttpDetection {
    pub fn new(base_url: String) -> Self {
        Self {
            base_url: base_url.trim_end_matches('/').to_owned(),
            client: reqwest::Client::new(),
        }
    }
}

#[derive(Debug, Deserialize)]
struct DetectJob {
    job_id: String,
    status: String,
    error: Option<String>,
}

#[async_trait]
impl DetectionAdapter for HttpDetection {
    async fn detect(&self, video: &Path, output: &Path) -> Result<EventsDocument> {
        let bytes = fs::read(video)
            .await
            .context("Could not read video for Detection.")?;
        let name = video
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("original.mp4")
            .to_owned();
        let part = reqwest::multipart::Part::bytes(bytes)
            .file_name(name)
            .mime_str("video/mp4")?;
        let form = reqwest::multipart::Form::new().part("video", part);
        let job: DetectJob = self
            .client
            .post(format!("{}/detect", self.base_url))
            .multipart(form)
            .send()
            .await
            .context("Detection service unreachable.")?
            .error_for_status()
            .context("Detection service rejected the upload.")?
            .json()
            .await
            .context("Detection service returned an invalid job.")?;
        let deadline = std::time::Instant::now() + Duration::from_secs(900);
        loop {
            let status: DetectJob = self
                .client
                .get(format!("{}/detect/{}", self.base_url, job.job_id))
                .send()
                .await
                .context("Detection service unreachable while polling.")?
                .error_for_status()?
                .json()
                .await?;
            match status.status.as_str() {
                "completed" => break,
                "failed" => bail!(
                    "Detection failed: {}",
                    status.error.unwrap_or_else(|| "unknown error".into())
                ),
                _ => {
                    ensure!(
                        std::time::Instant::now() < deadline,
                        "Detection timed out after 900 seconds."
                    );
                    tokio::time::sleep(Duration::from_millis(500)).await;
                }
            }
        }
        let events_bytes = self
            .client
            .get(format!("{}/detect/{}/events", self.base_url, job.job_id))
            .send()
            .await
            .context("Detection service unreachable while fetching events.")?
            .error_for_status()?
            .bytes()
            .await?;
        fs::write(output, &events_bytes)
            .await
            .context("Could not write events.json.")?;
        Ok(serde_json::from_slice(&events_bytes)
            .context("Detection service returned invalid events.json.")?)
    }
}

pub struct MockDetection;

#[async_trait]
impl DetectionAdapter for MockDetection {
    async fn detect(&self, video: &Path, output: &Path) -> Result<EventsDocument> {
        let metadata = probe(video).await?;
        let stream = metadata
            .streams
            .iter()
            .find(|s| s.codec_type == "video")
            .context("The uploaded file does not contain a video stream.")?;
        let width = stream.width.context("Video width is unavailable.")?;
        let height = stream.height.context("Video height is unavailable.")?;
        ensure!(
            width > 0 && height > 0 && width.max(height) <= 1920 && width.min(height) <= 1080,
            "Please upload a video up to 1080p (1920 by 1080, or portrait equivalent)."
        );
        let fps = stream
            .avg_frame_rate
            .as_deref()
            .and_then(parse_frame_rate)
            .or_else(|| stream.r_frame_rate.as_deref().and_then(parse_frame_rate))
            .context("Video frame rate is unavailable.")?;
        let duration: f64 = metadata
            .format
            .duration
            .context("Video duration is unavailable.")?
            .parse()
            .context("Video duration is invalid.")?;
        ensure!(
            duration.is_finite() && duration > 0.0,
            "Video duration must be finite and positive."
        );
        ensure!(
            duration <= 60.0,
            "Please upload a video of 60 seconds or less."
        );
        let timestamp = 1.0_f64.min(duration / 3.0);
        let events = EventsDocument {
            video: Video {
                id: video
                    .parent()
                    .and_then(|p| p.file_name())
                    .context("Missing video ID.")?
                    .to_string_lossy()
                    .into_owned(),
                duration,
                fps,
            },
            events: vec![Event {
                id: "test-tone-1".into(),
                t: timestamp,
                duration: 0.35_f64.min(duration - timestamp),
                event_type: EventType::Impact,
                prompt: "Mock impact / 880 Hz test tone".into(),
                volume: 0.8,
                pan: 0.0,
                confidence: 1.0,
            }],
            ambience: None,
        };
        events.validate()?;
        fs::write(output, serde_json::to_vec_pretty(&events)?).await?;
        Ok(events)
    }
}

/// Real generation can wait out several GPU cold starts, so this is far longer
/// than the 180 second limit in run_media.
const PYTHON_AUDIO_TIMEOUT: Duration = Duration::from_secs(3600);

/// Real Audio: runs the Python component in audio/ (`python -m musefx_audio`).
/// It reads RUNPOD_ENDPOINT_ID and RUNPOD_API_KEY from this process's environment.
pub struct PythonAudio {
    python: PathBuf,
    audio_dir: PathBuf,
    fake: bool,
}

impl PythonAudio {
    pub fn new(python: PathBuf, audio_dir: PathBuf, fake: bool) -> Self {
        Self {
            python,
            audio_dir,
            fake,
        }
    }

    /// Uses the virtual environment in the audio folder. With `fake`, sounds are
    /// synthetic, so the whole site can be tested without a GPU.
    pub fn from_dir(audio_dir: &Path, fake: bool) -> Result<Self> {
        let audio_dir = audio_dir
            .canonicalize()
            .with_context(|| format!("AUDIO_DIR {} does not exist.", audio_dir.display()))?;
        let python = audio_dir.join(".venv/bin/python");
        ensure!(
            python.is_file(),
            "{} is missing. Create it with the setup steps in audio/TESTING.md.",
            python.display()
        );
        Ok(Self::new(python, audio_dir, fake))
    }
}

#[async_trait]
impl AudioAdapter for PythonAudio {
    async fn render(&self, video: &Path, events: &EventsDocument, output: &Path) -> Result<()> {
        events.validate()?;
        // The command runs from the audio folder, so every path has to be absolute.
        let video = video
            .canonicalize()
            .context("Could not find the uploaded video.")?;
        let folder = output
            .parent()
            .context("The result path has no folder.")?
            .canonicalize()
            .context("Could not find the job folder.")?;
        let output = folder.join(output.file_name().context("The result path has no name.")?);
        let events_path = folder.join("audio_events.json");
        fs::write(&events_path, serde_json::to_vec_pretty(events)?)
            .await
            .context("Could not write events for Audio.")?;

        let mut command = Command::new(&self.python);
        command
            .args(["-m", "musefx_audio"])
            .arg(&video)
            .arg(&events_path)
            .arg("-o")
            .arg(&output)
            .current_dir(&self.audio_dir)
            .kill_on_drop(true)
            .stdin(Stdio::null());
        if self.fake {
            command.arg("--fake");
        }
        let finished = timeout(PYTHON_AUDIO_TIMEOUT, command.output()).await;
        let _ = fs::remove_file(&events_path).await;
        let finished = finished
            .context("Audio exceeded the 60 minute limit.")?
            .context("Could not start the Audio component.")?;
        if !finished.status.success() {
            let stderr = String::from_utf8_lossy(&finished.stderr);
            tracing::warn!(%stderr, "Audio component failed");
            let reason = stderr
                .lines()
                .rev()
                .find(|line| !line.trim().is_empty())
                .unwrap_or("unknown error");
            bail!("Audio failed: {}", reason.trim_start_matches("error: "));
        }
        Ok(())
    }
}

pub struct MockAudio;

#[async_trait]
impl AudioAdapter for MockAudio {
    async fn render(&self, video: &Path, events: &EventsDocument, output: &Path) -> Result<()> {
        events.validate()?;
        let has_audio = probe(video)
            .await?
            .streams
            .iter()
            .any(|s| s.codec_type == "audio");
        let mut filters = Vec::new();
        let mut inputs = if has_audio {
            "[0:a:0]".to_owned()
        } else {
            String::new()
        };
        for (i, event) in events
            .events
            .iter()
            .filter(|event| event.confidence >= 0.5)
            .enumerate()
        {
            let delay = (event.t * 1000.0).round();
            let gain = 0.4 * event.volume;
            let left = (1.0 - event.pan).sqrt();
            let right = (1.0 + event.pan).sqrt();
            filters.push(format!(
                "sine=frequency=880:sample_rate=48000:duration={},afade=t=out:st={}:d={},volume={gain},pan=stereo|c0={left}*c0|c1={right}*c0,adelay={delay}:all=1,asetpts=N/SR/TB[tone{i}]",
                event.duration, event.duration * 0.7, event.duration * 0.3));
            inputs.push_str(&format!("[tone{i}]"));
        }
        let count = events
            .events
            .iter()
            .filter(|event| event.confidence >= 0.5)
            .count()
            + usize::from(has_audio);
        if count > 0 {
            filters.push(format!(
                "{inputs}amix=inputs={count}:normalize=0,apad,asetpts=N/SR/TB[aout]"
            ));
        } else {
            filters.push("anullsrc=r=48000:cl=stereo[aout]".into());
        }
        run_media(
            Command::new("ffmpeg")
                .args(["-y", "-v", "error", "-i"])
                .arg(video)
                .args([
                    "-filter_complex",
                    &filters.join(";"),
                    "-map",
                    "0:v:0",
                    "-map",
                    "[aout]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-t",
                    &events.video.duration.to_string(),
                    "-movflags",
                    "+faststart",
                ])
                .arg(output),
        )
        .await?;
        Ok(())
    }
}

fn parse_frame_rate(value: &str) -> Option<f64> {
    let (numerator, denominator) = value.split_once('/')?;
    let fps = numerator.parse::<f64>().ok()? / denominator.parse::<f64>().ok()?;
    (fps.is_finite() && fps > 0.0).then_some(fps)
}
