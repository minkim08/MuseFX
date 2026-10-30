import pathlib

import pytest

from detection.events import Event, map_type, postprocess, validate
from detection.sam_parse import motion_peaks, parse_output

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_sam_parse_real_fixture():
    text = (FIXTURES / "sam_image_output.txt").read_text()
    tracks = parse_output(text)
    assert tracks, "expected at least one track from real SAM output"
    pts = next(iter(tracks.values()))
    p = pts[0]
    # Synthetic image was a red rectangle drawn at 200,100 - 440,260 (inclusive)
    assert (p.x1, p.y1) == (200, 100)
    assert abs(p.x2 - 439) <= 1 and abs(p.y2 - 259) <= 1
    assert -1.0 <= p.pan <= 1.0


def test_sam_parse_zero_matches():
    assert parse_output("") == {}
    assert parse_output("no records here") == {}


def test_motion_peaks_finds_stop():
    from detection.sam_parse import BoxPoint
    # Object moves right fast, then stops: speed change should peak near stop
    pts = []
    x = 0
    for f in range(10):
        if f < 5:
            x += 40
        pts.append(BoxPoint(frame=f, object_id=0, x1=x, y1=0, x2=x + 20, y2=20, w=640, h=360))
    peaks = motion_peaks(pts, fps=10.0)
    assert peaks
    assert abs(peaks[0][0] - 0.5) <= 0.2


def ev(t, dur=0.4, typ="impact", conf=0.9):
    return Event(id="tmp", t=t, duration=dur, type=typ, prompt="p", volume=0.8, pan=0.0, confidence=conf)


def test_postprocess_merges_clamps_and_maps():
    out = postprocess([ev(1.0), ev(1.05), ev(9.9, typ="collision")], duration=10.0)
    assert len(out) == 2, "events 50ms apart must merge"
    assert out[1].type == "impact", "collision must map onto contract types"
    assert out[1].t + out[1].duration <= 10.0 + 1e-9
    assert [e.id for e in out] == ["e1", "e2"]


def test_map_type():
    assert map_type("footstep") == "footstep"
    assert map_type("Click") == "ui"
    assert map_type("explosion") == "impact"
    assert map_type("something-new") == "other"


def _doc(events):
    return {"video": {"id": "v", "duration": 10.0, "fps": 30.0},
            "events": [e.__dict__ for e in events]}


def test_validate_accepts_and_rejects():
    validate(_doc(postprocess([ev(1.0)], 10.0)))
    bad = _doc([ev(1.0)])
    bad["events"][0]["type"] = "explosion"
    with pytest.raises(ValueError):
        validate(bad)
    bad2 = _doc([ev(9.9, dur=0.4)])
    with pytest.raises(ValueError):
        validate(bad2)


def test_emitted_shape_matches_contract_types():
    doc = _doc(postprocess([ev(1.0)], 10.0))
    validate(doc)
    assert doc["events"][0]["type"] in {"footstep", "impact", "hit", "door", "pickup", "ui", "whoosh", "other"}


def test_service_flow_with_fake_pipeline(monkeypatch):
    import detection.service as svc

    def fake_detect(video, video_id, progress=None, **kw):
        if progress:
            progress("detecting", 15)
            progress("describing", 55)
        return {"video": {"id": video_id, "duration": 4.0, "fps": 30.0},
                "events": [{"id": "e1", "t": 1.0, "duration": 0.35, "type": "impact",
                            "prompt": "tone", "volume": 0.8, "pan": 0.0, "confidence": 1.0}]}

    monkeypatch.setattr(svc, "detect", fake_detect)
    from fastapi.testclient import TestClient
    client = TestClient(svc.app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/detect", files={"video": ("x.txt", b"no")}).status_code == 415
    r = client.post("/detect", files={"video": ("clip.mp4", b"fake-mp4")})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    status = client.get(f"/detect/{job_id}").json()
    assert status["status"] == "completed" and status["progress"] == 100
    events = client.get(f"/detect/{job_id}/events").json()
    assert events["events"][0]["type"] == "impact"
    assert client.get("/detect/nope").status_code == 404
