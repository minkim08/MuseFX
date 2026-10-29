# Product — Component 3: App and Integration

**Owner:** Person 3 · **Runs on:** Modal (backend web endpoint) + web frontend

Product owns everything the user and judges see, and connects Detection and Audio into one pipeline.

Shared contract and project context: [Agents.md](../Agents.md) · Upstream: [Detection](../detection/README.md) · [Audio](../audio/README.md)

## Backend

FastAPI service with the three endpoints from the interface contract in [Agents.md](../Agents.md#backend-api):

| Endpoint | Does |
|---|---|
| `POST /jobs` | Upload a video, returns `job_id` |
| `GET /jobs/{id}` | Returns stage (`detecting`, `describing`, `generating`, `mixing`, `done`, `failed`) and progress 0–100 |
| `GET /jobs/{id}/result` | Returns the finished MP4 and `events.json` |

- Runs the pipeline as a **background job**: Detection, then Audio, updating the stage and progress as it goes (the web app polls `GET /jobs/{id}`).
- Stores uploads, `events.json`, and results in a **Modal Volume or S3 bucket, keyed by `job_id`**.
- **Simplest option:** deploy the whole backend on **Modal as a web endpoint**, so there's one platform to manage (Audio generation already runs on Modal — see [audio/README.md](../audio/README.md)).

Stage names shown to the user map to the API stages: finding events (`detecting`), describing sounds (`describing`), generating audio (`generating`), mixing (`mixing`).

## Web app

One page only. No login.

- An **upload box** — silent MP4, up to 60 seconds, up to 1080p.
- A **progress bar with stage names** (finding events, describing sounds, generating audio, mixing), driven by `GET /jobs/{id}`.
- **Side-by-side players** for the original and the result.
- An **event timeline** under the player: a marker per event from `events.json`; hovering shows its `type` and `prompt`.
- A **download** for the finished MP4.
- **Stretch goal:** click an event to regenerate its sound or switch to another of its options (Audio generates 3 options per event).

Plain **React or Next.js**; no login, accounts, or payments (non-goals in [Agents.md](../Agents.md)).

## Integration responsibilities

- `events.json` is the only handoff between Detection and Audio — Product passes it through and stores it per `job_id`; it does not invent or rewrite events. Schema changes need everyone's agreement.
- End-to-end flow to keep working at all times: upload → progress → side-by-side result → download, as in the demo story in [Agents.md](../Agents.md#user-flow-and-demo-story).

## Testing this component alone

- Until Detection works, test with a **hand-written `events.json` for one sample clip** (per [Agents.md](../Agents.md)): upload, poll progress through all stages to `done`, play side by side, show timeline markers, and download the MP4.
- Exercise the `failed` stage too: a bad upload should surface as `failed`, not a stuck progress bar.
