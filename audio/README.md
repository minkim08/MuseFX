# Audio — Component 2: Events → Finished Video

**Owner:** Person 2 · **Runs on:** Runpod Serverless GPUs (generation) + CPU (mixing)

Audio turns `events.json` into a finished MP4. Generating the sounds runs on Runpod GPUs; mixing runs on CPU.

**Status: built and connected.** Product's backend runs this component when `AUDIO_DIR` is set, through its `PythonAudio` adapter; see [INTEGRATION.md](../product/docs/INTEGRATION.md). Without it, the backend uses Mock Audio.

Shared contract and project context: [Agents.md](../Agents.md) · Upstream: [Detection](../detection/README.md)

## Input / Output

| | |
|---|---|
| **Input** | The original MP4 and `events.json` (produced by Detection; schema frozen in [Agents.md](../Agents.md#interface-contract-eventsjson)) |
| **Output** | The MP4 with a mixed audio track — returned to the web app via the Backend `GET /jobs/{id}/result` |

Rules from the contract that Audio owns:

- Skip events with `confidence` below 0.5.
- Each sound's attack lands at its event `t`; lay the optional `ambience` bed underneath the whole clip.

## Generation on Runpod

1. Deploy **AudioGen** (from Meta's AudioCraft) as a **Runpod Serverless worker** ([runpod](runpod)), with the weights baked into the image so cold starts don't download them again.
2. Expose `generate(prompts, durations, n_candidates)` and run **all events in one batch**.
3. Generate **3 options per event** in parallel as GPU batches (prompts × options) in each request.
4. **Pick the best option:** it has the sharpest start (for impacts and footsteps) and the closest match to the prompt. Start with a simple loudness-and-onset score; add an audio-text similarity model only if time allows.
5. **Cache results by prompt and duration**, so repeated footstep prompts reuse audio.

## Mixer (CPU)

1. **Trim leading silence** from each sound so its start lands exactly on `t`.
2. Apply a **5 ms fade-in** and a **20–50 ms fade-out** to avoid clicks.
3. **Vary pitch slightly (±2 semitones) and volume (±3 dB)** between repeated footsteps so they don't sound robotic.
4. Apply `volume` and `pan` (from `events.json`), then lay the **ambience bed** underneath at low volume.
5. **Limit the final mix** to avoid clipping (peak at **-1 dBFS**).
6. Merge the audio into the video with **ffmpeg**, mixing it over any audio the clip already has; the video stream is **copied, not re-encoded**.

## Tools

Python, Runpod Serverless, AudioCraft, `numpy`/`scipy` for mixing, `ffmpeg`.

## Watch out for

- **Licence:** AudioGen weights are licensed for **non-commercial use only**. MuseFX is a non-commercial hackathon project and uses them on that basis; do not reuse or present this stack as commercial-ready.
- **Cold starts:** a cold start can take **5 minutes or more**. The client submits a job and polls it, retrying network failures, and waits up to 30 minutes per job, so a slow start only means a longer wait. Anything that calls Audio needs a timeout to match; Product's `PythonAudio` adapter allows 60 minutes.
- **Before a live demo:** send one request first to wake a worker, or set **Active workers** to 1 for the demo (this bills continuously, so set it back to 0 afterwards).

## Testing this component alone

- Until Detection works, test against a hand-written `events.json` for one sample clip (per [Agents.md](../Agents.md)): check each sound's attack lands on `t`, repeated footsteps vary, and the mix peaks at -1 dBFS without clipping.
- Verify the output MP4's video stream is copied, not re-encoded (no quality loss, fast merge).

## Running it

```bash
cd audio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
```

Offline, with synthetic sounds instead of AudioGen (no GPU or account needed):

```bash
.venv/bin/python -m musefx_audio clip.mp4 fixtures/sample_events.json -o out.mp4 --fake
```

With AudioGen on Runpod, deploy the worker and set `RUNPOD_ENDPOINT_ID` and `RUNPOD_API_KEY`, then drop `--fake`. See step 5 of [TESTING.md](TESTING.md).

From Python:

```python
from musefx_audio import render
from musefx_audio.generators import RunPodGenerator

render("in.mp4", "events.json", "out.mp4", RunPodGenerator(),
       on_progress=lambda stage, fraction: ...)  # stage: "generating" or "mixing"
```

Picked sounds are cached in `audio/.cache/sounds` by prompt and duration, with a separate folder per generator so fake sounds are never reused by a real run. Pass `cache_dir` to put the cache on a shared volume.
