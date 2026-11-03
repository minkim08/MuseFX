use std::{fs, os::unix::fs::PermissionsExt, path::Path};

use uuid::Uuid;
use video_to_sfx::{
    adapters::{AudioAdapter, PythonAudio},
    models::{EventsDocument, Video},
};

fn events() -> EventsDocument {
    EventsDocument {
        video: Video {
            id: "clip".into(),
            duration: 2.0,
            fps: 30.0,
        },
        events: vec![],
        ambience: None,
    }
}

/// A stand-in for Python that records how it was called, then runs `body`.
fn stub(folder: &Path, body: &str) -> std::path::PathBuf {
    let path = folder.join("python");
    let script = format!(
        "#!/bin/sh\npwd > \"{folder}/cwd\"\necho \"$@\" > \"{folder}/args\"\n{body}\n",
        folder = folder.display()
    );
    fs::write(&path, script).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    path
}

fn temp_folder() -> std::path::PathBuf {
    let path = std::env::temp_dir().join(format!("python-audio-{}", Uuid::new_v4()));
    fs::create_dir(&path).unwrap();
    path.canonicalize().unwrap()
}

#[tokio::test]
async fn runs_the_audio_component_from_its_own_folder() {
    let folder = temp_folder();
    let audio_dir = folder.join("audio");
    fs::create_dir(&audio_dir).unwrap();
    let video = folder.join("original.mp4");
    fs::write(&video, b"video").unwrap();
    let output = folder.join("result.mp4");
    let python = stub(&folder, &format!("echo done > \"{}\"", output.display()));

    PythonAudio::new(python, audio_dir.clone(), true)
        .render(&video, &events(), &output)
        .await
        .unwrap();

    assert!(output.is_file());
    let args = fs::read_to_string(folder.join("args")).unwrap();
    assert!(args.starts_with("-m musefx_audio"));
    assert!(args.contains(video.to_str().unwrap()));
    assert!(args.contains(&format!("-o {}", output.display())));
    assert!(args.contains("--fake"));
    assert_eq!(
        fs::read_to_string(folder.join("cwd")).unwrap().trim(),
        audio_dir.to_str().unwrap()
    );
    assert!(!folder.join("audio_events.json").exists());
    fs::remove_dir_all(folder).unwrap();
}

#[tokio::test]
async fn reports_why_the_audio_component_failed() {
    let folder = temp_folder();
    let video = folder.join("original.mp4");
    fs::write(&video, b"video").unwrap();
    let python = stub(
        &folder,
        "echo generating >&2\necho 'error: Runpod job failed' >&2\nexit 1",
    );

    let error = PythonAudio::new(python, folder.clone(), false)
        .render(&video, &events(), &folder.join("result.mp4"))
        .await
        .unwrap_err();

    assert_eq!(error.to_string(), "Audio failed: Runpod job failed");
    assert!(!fs::read_to_string(folder.join("args"))
        .unwrap()
        .contains("--fake"));
    fs::remove_dir_all(folder).unwrap();
}

#[test]
fn a_missing_virtual_environment_stops_startup() {
    let folder = temp_folder();
    assert!(PythonAudio::from_dir(&folder, false).is_err());
    assert!(PythonAudio::from_dir(&folder.join("nope"), false).is_err());
    fs::remove_dir_all(folder).unwrap();
}
