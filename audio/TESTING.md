# Testing Audio (Component 2)

Steps 1–4 test the whole Audio pipeline with synthetic sounds, with no GPU or account. Step 5 tests real AudioGen on Runpod, and step 6 tests it with real Detection output.

## Before you start

Set up once. Run this and every later command from the `audio` folder:

```bash
cd audio
```

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

ffmpeg comes with the requirements, so you don't need to install it separately. A system ffmpeg is used if you have one.

## 1. Run the automated tests

```bash
.venv/bin/python -m pytest
```

**Expected:** all 33 tests pass. They check that sounds start on their event time, the mix stays under -1 dBFS, fades prevent clicks, repeated sounds vary, the video stream is copied unchanged, and a 6-minute cold start with network failures still succeeds.

## 2. Make a test clip

The commands below create an 8-second silent clip that matches `fixtures/sample_events.json`. The picture is only a test pattern, so the sounds won't match what you see. `outputs/` is ignored by git.

```bash
mkdir -p outputs
```

```bash
.venv/bin/python -c "import subprocess; from musefx_audio.merge import ffmpeg_exe; subprocess.run([ffmpeg_exe(), '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', 'outputs/sample.mp4'], check=True)"
```

## 3. Run with fake sounds

```bash
.venv/bin/python -m musefx_audio outputs/sample.mp4 fixtures/sample_events.json -o outputs/fake.mp4 --fake
```

**Expected last line:**

```text
wrote outputs/fake.mp4: 9 events played, 1 skipped, 7 sounds generated, 0 from cache
```

Run the same command again. The second run should reuse the saved sounds:

```text
wrote outputs/fake.mp4: 9 events played, 1 skipped, 0 sounds generated, 7 from cache
```

Open the result and listen:

```bash
open outputs/fake.mp4
```

Fake sounds are short synthetic bursts, so only check timing and level here. They are cached separately from real sounds, so they can't end up in a real run.

| Time (s) | You should hear |
|---|---|
| Whole clip | A quiet background that fades in and out |
| 0.50, 0.95, 1.40, 1.85 | 4 footsteps, slightly different from each other, moving left to centre-right |
| 2.60 | Whoosh |
| 2.90 | Loud impact, slightly right |
| 4.20 | Door, right side |
| 5.50 | Pickup |
| 6.30 | Quiet UI click |
| 7.00 | Nothing: this event's confidence is 0.3, so it is skipped |

There should be no clicks, pops or distortion. The picture should look identical to `outputs/sample.mp4`.

## 4. Check bad input

Missing Runpod credentials:

```bash
env -u RUNPOD_ENDPOINT_ID -u RUNPOD_API_KEY .venv/bin/python -m musefx_audio outputs/sample.mp4 fixtures/sample_events.json -o outputs/x.mp4
```

**Expected:** `error: Set RUNPOD_ENDPOINT_ID and RUNPOD_API_KEY (or use --fake).`

An invalid `events.json`:

```bash
sed 's/"pan": -0.2/"pan": -5/' fixtures/sample_events.json > outputs/bad.json
```

```bash
.venv/bin/python -m musefx_audio outputs/sample.mp4 outputs/bad.json -o outputs/x.mp4 --fake
```

**Expected:** `error: events[0] (e1): pan must be between -1 and 1, got -5.0`

## 5. Test real AudioGen on Runpod

This step hasn't been run yet, so expect to fix problems on the first deploy. Real runs cost money: Runpod lists 16 GB Serverless GPUs at about $0.58/hour, billed while a worker is running, including during cold starts.

### Deploy the worker

1. In Runpod, add funds and create an API key under **Settings → API Keys**.
2. Connect GitHub under **Settings → Connections**, and give Runpod access to the `minkim08/MuseFX` repo. The repo owner may need to approve this.
3. Go to **Serverless → New Endpoint**, choose the GitHub repo, and set:

    | Setting | Value |
    |---|---|
    | Branch | `main` |
    | Dockerfile Path | `audio/runpod/Dockerfile` |
    | Build Context | The repo root |
    | GPU | 16 GB or more |
    | Active workers | 0 |
    | Max workers | 1 |
    | FlashBoot | On |

4. Wait for the build to finish. Copy the **endpoint ID** from the endpoint page.

Pushing to GitHub doesn't update the endpoint. To deploy a change, create a new GitHub release.

### Run it

Set your credentials in the same terminal. The tool doesn't read `.env` files.

```bash
export RUNPOD_ENDPOINT_ID=your_endpoint_id
```

```bash
export RUNPOD_API_KEY=your_api_key
```

Then run without `--fake`:

```bash
.venv/bin/python -m musefx_audio outputs/sample.mp4 fixtures/sample_events.json -o outputs/real.mp4
```

The first run has to start a GPU worker, which can take 5 minutes or more. That's normal: every 30 seconds it prints `waiting for a GPU worker (...)`, and it waits up to 30 minutes per job.

**Expected last line on the first run:**

```text
wrote outputs/real.mp4: 9 events played, 1 skipped, 7 sounds generated, 0 from cache
```

Listen using the table in step 3, and also check that each sound matches its prompt in `fixtures/sample_events.json`. For example, the 5.50 sound should be a bright coin chime.

If it fails, the error names the problem, such as a bad API key or the worker running out of GPU memory. For out-of-memory errors, lower `AUDIOGEN_MAX_BATCH` in the endpoint's environment variables. If it waits the full 30 minutes, the worker probably crashed while starting, so check the endpoint's logs.

Real sounds are saved in `.cache/sounds/audiogen`. To generate them fresh after changing the worker, clear that folder first:

```bash
rm -rf .cache/sounds/audiogen
```

## 6. Test with real Detection output

Run Detection on a real clip by following [detection/README.md](../detection/README.md), which produces an `events.json`. Then pass that file and the same clip to Audio:

```bash
.venv/bin/python -m musefx_audio path/to/clip.mp4 path/to/events.json -o outputs/clip.mp4
```

Check that sounds land on the actions you see. A sound 100 ms off feels wrong. If timing is off, compare the `t` values in `events.json` with the video to tell whether the problem is in Detection or Audio.

## Known limitations

- Audio isn't connected to the backend yet. Product's Mock Audio test tone stays in place until a Rust `AudioAdapter` calls this component.
- Any audio already in the input clip is replaced, not kept, so test with silent clips for now.
