# Audio — Component 2: Events → Finished Video

**Owner:** Person 2 · **Runs on:** Modal GPUs (generation) + CPU (mixing)

Audio turns `events.json` into a finished MP4. Generating the sounds runs on Modal GPUs; mixing runs on CPU.

**Status: not yet implemented.** Until this component is built, Product's Mock Audio stands in for it: an 880 Hz test tone at each event time (volume/pan applied, confidence < 0.5 skipped, video stream copied). That tone is a timing placeholder, not generated sound.

Shared contract and project context: [Agents.md](../Agents.md) · Upstream: [Detection](../detection/README.md)

## Input / Output

| | |
|---|---|
| **Input** | The original MP4 and `events.json` (produced by Detection; schema frozen in [Agents.md](../Agents.md#interface-contract-eventsjson)) |
| **Output** | The MP4 with a mixed audio track — returned to the web app via the Backend `GET /jobs/{id}/result` |

Rules from the contract that Audio owns:

- Skip events with `confidence` below 0.5.
- Each sound's attack lands at its event `t`; lay the optional `ambience` bed underneath the whole clip.

## Generation on Modal

1. Deploy **AudioGen** (from Meta's AudioCraft) as a **Modal class** with the weights cached in a **Modal Volume**, so cold starts don't download them again.
2. Expose `generate(prompts, durations, n_candidates)` and run **all events in one batch**.
3. Generate **3 options per event** in parallel using Modal's `.map()`.
4. **Pick the best option:** it has the sharpest start (for impacts and footsteps) and the closest match to the prompt. Start with a simple loudness-and-onset score; add an audio-text similarity model only if time allows.
5. **Cache results by prompt and duration**, so repeated footstep prompts reuse audio.

## Mixer (CPU)

1. **Trim leading silence** from each sound so its start lands exactly on `t`.
2. Apply a **5 ms fade-in** and a **20–50 ms fade-out** to avoid clicks.
3. **Vary pitch slightly (±2 semitones) and volume (±3 dB)** between repeated footsteps so they don't sound robotic.
4. Apply `volume` and `pan` (from `events.json`), then lay the **ambience bed** underneath at low volume.
5. **Limit the final mix** to avoid clipping (peak at **-1 dBFS**).
6. Merge the audio into the video with **ffmpeg**; the video stream is **copied, not re-encoded**.

## Tools

Python, Modal, AudioCraft, `pydub` or `numpy` for mixing, `ffmpeg`.

## Watch out for

- **Licence:** AudioGen weights are licensed for **non-commercial use only**. MuseFX is a non-commercial hackathon project and uses them on that basis; do not reuse or present this stack as commercial-ready.
- **Cold starts:** GPU cold starts can take **30–60 seconds**; warm the container before the live demo.

## Testing this component alone

- Until Detection works, test against a hand-written `events.json` for one sample clip (per [Agents.md](../Agents.md)): check each sound's attack lands on `t`, repeated footsteps vary, and the mix peaks at -1 dBFS without clipping.
- Verify the output MP4's video stream is copied, not re-encoded (no quality loss, fast merge).
