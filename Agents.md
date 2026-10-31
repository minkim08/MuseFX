# MuseFX — Project Context for Agents

## Overview

MuseFX takes a silent video and returns it with AI-generated sound effects synced to the action.

- **Team:** 3 people
- **Deadline:** Meta Global AI Developer Hackathon (indie developer track)
- **Name:** MuseFX

## Who it's for

Indie game developers and small creators who make gameplay clips and trailers.

Adding sound effects by hand takes hours, so most skip it and their videos feel flat.

## Goals

- A working web app: upload a clip of up to 60 seconds, get back the same clip with synced sound effects.
- Strong results on game footage: footsteps, impacts, hits, doors, pickups, and UI clicks.
- Meta AI at the core of the product:
  - SAM 3.1 and Muse Spark through the Meta Model API
  - Meta's open-weights AudioGen for sound generation (hosting: see Architecture note below)
- A demo video that plays the silent clip, then the version with generated sound.

## Non-goals for the hackathon

- Music, voice, or dialogue generation.
- Training our own model from scratch.
- Long videos, live streams, or real-time processing.
- User accounts, payments, or a public launch.

Do not add these without explicit agreement from all three builders.

## User flow and demo story

The whole product is one screen: upload a clip, wait, then compare the before and after.

1. The user uploads a silent clip (MP4, up to 60 seconds, up to 1080p).
2. A progress bar shows each stage: finding events, describing sounds, generating audio, mixing.
3. The result plays side by side with the original, with event markers on a timeline under the player.
4. The user downloads the finished MP4.
5. **Stretch goal:** click an event to regenerate its sound or switch to another of its options.

## System architecture

See [image.png](image.png) — "System architecture".

One upload runs through three owned stages, joined by `events.json`. Each person can build and test their part alone because `events.json` is the only handoff between Detection and Audio.

| Part | Owner | What it does |
|---|---|---|
| Product | Person 3 | Web app (upload, progress, side-by-side) + Backend (FastAPI on Modal, runs each job): web app uploads video to backend |
| Detection | Person 1 · Meta Model API | Extract frames (ffmpeg, 10 fps) → Understand scene (Muse Spark) → Track and time (SAM 3.1) → Write prompts (Muse Spark) → emits `events.json` |
| Audio | Person 2 · GPUs | Generate options (AudioGen, 3 per event, in parallel) → Pick the best (sharpest start, best match, cached by prompt) → Mix and merge (fades, pan and volume, ffmpeg into the MP4) → Finished MP4 to the web app |

**Hosting (confirmed):** AudioGen generation runs on **Baseten GPUs** (we have free Baseten credits; mixing on CPU); the Backend is **FastAPI on Modal**, per the diagram. Earlier "AudioGen on Modal" wording is superseded.

## Team contract: roles, handoffs, and done

Each person owns one part end to end, hands off one artifact, and is done against one testable bar. This is a contract: the other two build against your handoff, so keep it stable (see `events.json` below).

| Role | Owns | Hands off | Done when |
|---|---|---|---|
| Person 1: Detection | Frame extraction, Muse Spark scene and prompts, SAM 3.1 tracking and timing | `events.json` for any clip | Events on 5 test clips land within 100 ms of the real moment |
| Person 2: Audio | AudioGen on Baseten, picking the best option, the mixer and ffmpeg merge | Finished MP4 with sound | A clean mix with no clicks or clipping from a hand-written `events.json` |
| Person 3: Product | Backend and job API, web app, test clips, demo video, submission | Live app and demo video | Upload to result works in the browser for any test clip |

## Interface contract: `events.json`

> All three parts connect through one file, `events.json`. Agree on it before anyone writes code, and change it only with everyone's agreement.

`events.json` is produced by Detection and used by Audio:

```json
{
  "video": { "id": "abc123", "duration": 24.5, "fps": 30 },
  "events": [
    {
      "id": "e1",
      "t": 2.35,
      "duration": 0.4,
      "type": "footstep",
      "prompt": "heavy boot on gravel, outdoors",
      "volume": 0.8,
      "pan": -0.3,
      "confidence": 0.9
    }
  ],
  "ambience": { "prompt": "light wind, distant birds", "volume": 0.3 }
}
```

| Field | Meaning |
|---|---|
| `t` | Start time in seconds; the sound's attack lands here |
| `duration` | Sound length in seconds |
| `type` | One of: `footstep`, `impact`, `hit`, `door`, `pickup`, `ui`, `whoosh`, `other` |
| `prompt` | Text description sent to AudioGen |
| `volume` | 0 to 1 |
| `pan` | -1 (left) to 1 (right), from where the object is on screen |
| `confidence` | 0 to 1; Audio can skip events below 0.5 |
| `ambience` | Optional background bed for the whole clip |

Until Detection works, Audio and Product test with a hand-written `events.json` for one sample clip.

## Backend API

Built by Product, called by the web app.

| Endpoint | Does |
|---|---|
| `POST /jobs` | Upload a video, returns `job_id` |
| `GET /jobs/{id}` | Returns stage (`detecting`, `describing`, `generating`, `mixing`, `done`, `failed`) and progress 0–100 |
| `GET /jobs/{id}/result` | Returns the finished MP4 and `events.json` |

## Working agreements for agents

- Keep scope to the hackathon goals above; prefer a working end-to-end demo over extra features.
- The MIT licence in [LICENSE](LICENSE) covers MuseFX code only — never commit model weights, credentials, uploads, or game footage (see [.gitignore](.gitignore)).
- Treat `events.json` and the Backend API above as frozen contracts — change them only with everyone's agreement, and update this file when they change.
- Build and test each component independently against the contract (hand-written `events.json` fixture for one sample clip) before integrating.
- Component READMEs hold per-component details; this file stays the shared overview and contract:
  - Detection: [detection/README.md](detection/README.md)
  - Audio: [audio/README.md](audio/README.md)
  - Product: [product/README.md](product/README.md)
