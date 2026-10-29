use video_to_sfx::models::{Event, EventsDocument, EventType, Video};

fn document() -> EventsDocument {
    EventsDocument { video: Video { id: "test".into(), duration: 2.0, fps: 30.0 },
        events: vec![Event { id: "impact-1".into(), t: 1.0, duration: 0.35,
            event_type: EventType::Impact, prompt: "Boot on gravel".into(), volume: 0.8,
            pan: -0.3, confidence: 0.9 }], ambience: None }
}

#[test]
fn validates_team_fixture_and_empty_events() {
    let example: EventsDocument = serde_json::from_str(include_str!("../../docs/events.example.json")).unwrap();
    example.validate().unwrap();
    let mut value = document();
    value.events.clear();
    value.validate().unwrap();
    let json = serde_json::to_value(document()).unwrap();
    assert_eq!(json["events"][0]["type"], "impact");
    assert_eq!(json["events"][0]["t"], 1.0);
    assert!(json.get("version").is_none());
}

#[test]
fn rejects_out_of_bounds_duplicate_and_invalid_values() {
    let baseline = document();
    baseline.validate().unwrap();
    let mut invalid = vec![];
    let mut d = baseline.clone(); d.video.duration = 1.0; invalid.push(d);
    let mut d = baseline.clone(); d.events.push(d.events[0].clone()); invalid.push(d);
    let mut d = baseline.clone(); d.events[0].t = f64::INFINITY; invalid.push(d);
    let mut d = baseline.clone(); d.events[0].confidence = f64::NAN; invalid.push(d);
    let mut d = baseline.clone(); d.events[0].confidence = 1.1; invalid.push(d);
    let mut d = baseline.clone(); d.events[0].duration = -1.0; invalid.push(d);
    let mut d = baseline.clone(); d.events[0].id.clear(); invalid.push(d);
    let mut d = baseline.clone(); d.events[0].prompt.clear(); invalid.push(d);
    let mut d = baseline.clone(); d.video.duration = f64::INFINITY; invalid.push(d);
    let mut d = baseline.clone(); d.video.fps = 0.0; invalid.push(d);
    let mut d = baseline.clone(); d.events[0].volume = 1.1; invalid.push(d);
    let mut d = baseline; d.events[0].pan = -1.1; invalid.push(d);
    for value in invalid { assert!(value.validate().is_err(), "accepted {value:?}"); }
}

#[test]
fn rejects_unknown_event_types() {
    let mut value = serde_json::to_value(document()).unwrap();
    value["events"][0]["type"] = "explosion".into();
    assert!(serde_json::from_value::<EventsDocument>(value).is_err());
}
