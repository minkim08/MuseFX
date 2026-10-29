# Detection — Component 1: Video → Events

**Owner:** Person 1 · **Runs on:** Meta Model API (Muse Spark + SAM 3.1) + local/backend `ffmpeg`

Detection turns a video into `events.json`. It is the hardest part, because a sound even 100 ms off feels wrong — timing precision is the priority for this component.

Shared contract and project context: [Agents.md](../Agents.md)

## Input / Output

| | |
|---|---|
| **Input** | An MP4 (up to 60 seconds, up to 1080p, from the Backend `POST /jobs`) |
| **Output** | `events.json` — the only handoff to Audio. Schema and field meanings are frozen in [Agents.md](../Agents.md#interface-contract-eventsjson); change only with everyone's agreement |

## Pipeline

### 1. Extract frames

- Run `ffmpeg` at **10 fps** for analysis.
- Keep **full fps** for refining timing later (step 4).

### 2. Understand the scene

- Send keyframes (about **1 per second**) to **Muse Spark**.
- Ask for:
  - The setting (indoor or outdoor, surface type)
  - The main moving objects
  - A list of likely sound events with rough times

### 3. Track objects

- Use **SAM 3.1** to segment and track the objects Muse Spark named — e.g. `"player character"`, `"ball"`, `"door"` — across frames.

### 4. Find exact moments

From each tracked object's motion, detect sharp changes:

| Signal | Event |
|---|---|
| Sudden stop or change of direction | Impact |
| Foot reaching its lowest point | Footstep |
| Two masks touching | Collision |

- Refine each time to the exact frame using full-fps frames, so the sound's attack (`t` in `events.json`) lands on the frame where the action happens.

### 5. Write sound prompts

- For each event, send **Muse Spark 3 frames around it** plus the scene description.
- Ask for: an AudioGen `prompt`, a `duration`, and a `volume`.
- Ask for **JSON output**.

### 6. Set position

- Set `pan` from the object's horizontal position on screen (`-1` = left, `1` = right, per the `events.json` contract).

## Ways to improve quality

- **Merge** events closer together than **80 ms** so sounds don't double up.
- Keep a **fixed list of event types** (`footstep`, `impact`, `hit`, `door`, `pickup`, `ui`, `whoosh`, `other`) so prompts stay consistent; footsteps use the same surface prompt for the whole clip.
- Have **Muse Spark check the final event list against keyframes** and remove false positives.
- Set `confidence` honestly (0–1): Audio can skip events below 0.5, so low-confidence guesses should be marked, not hidden.

## Fallback

If tracking is unreliable: have Muse Spark pick events straight from frames at 10 fps, with lower timing precision.

Ship the fallback rather than no output — a roughly-timed event list still lets Audio and Product demo end to end.

## Open questions

Verify against the Meta Model API before writing pipeline code:

- [ ] Whether Muse Spark accepts video directly or only images
- [ ] Request size limits (frames per request, image size, payload size)
- [ ] How SAM 3.1 takes text prompts through the Meta Model API (prompt format for named objects like `"player character"`)

Record the answers here when known, since steps 2, 3, and 5 depend on them.

## Testing this component alone

- Run Detection on one sample gameplay clip and inspect the emitted `events.json` by hand: are `t` values on the exact action frames (foot down, contact, stop)?
- Until the Backend exists, Detection can be run standalone on a local MP4; Audio and Product test in parallel against a hand-written `events.json` for the same sample clip, so no one waits on this component.
