# Testing Audio (Component 2)

Steps 1–4 test the whole Audio pipeline with synthetic sounds, with no GPU or account. Step 5 tests real AudioGen on Baseten, and step 6 tests it with real Detection output.

## Before you start

The Audio code is in [PR #5](https://github.com/minkim08/MuseFX/pull/5). Until it merges, test on its branch:

```bash
git fetch origin
```

```bash
git switch audio/component-2
```

Then set up once. Run this and every later command from the `audio` folder:

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

**Expected:** all 23 tests pass. They check that sounds start on their event time, the mix stays under -1 dBFS, fades prevent clicks, repeated sounds vary, and the video stream is copied unchanged.

## 2. Make a test clip

The commands below create an 8-second silent clip that matches `fixtures/sample_events.json`. The picture is only a test pattern, so the sounds won't match what you see. `outputs/` is ignored by git.

```bash
mkdir -p outputs
```

```bash
.venv/bin/python -c "import subprocess; from musefx_audio.merge import ffmpeg_exe; subprocess.run([ffmpeg_exe(), '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', 'outputs/sample.mp4'], check=True)"
```

## 3. Run with fake sounds

Use the separate `.cache/fake` folder. See the warning under step 5 for why.

```bash
.venv/bin/python -m musefx_audio outputs/sample.mp4 fixtures/sample_events.json -o outputs/fake.mp4 --fake --cache-dir .cache/fake
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

Fake sounds are short synthetic bursts, so only check timing and level here.

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

Missing Baseten credentials:

```bash
env -u BASETEN_MODEL_ID -u BASETEN_API_KEY .venv/bin/python -m musefx_audio outputs/sample.mp4 fixtures/sample_events.json -o outputs/x.mp4
```

**Expected:** `error: Set BASETEN_MODEL_ID and BASETEN_API_KEY (or use --fake).`

An invalid `events.json`:

```bash
sed 's/"pan": -0.2/"pan": -5/' fixtures/sample_events.json > outputs/bad.json
```

```bash
.venv/bin/python -m musefx_audio outputs/sample.mp4 outputs/bad.json -o outputs/x.mp4 --fake --cache-dir .cache/fake
```

**Expected:** `error: events[0] (e1): pan must be between -1 and 1, got -5.0`

## 5. Test real AudioGen on Baseten

> **Warning:** the cache doesn't record whether a sound was fake or real. If `--fake` and a real run share a cache folder, the real run silently reuses the fake sounds and reports them as `from cache`. Only use `--fake` with `--cache-dir .cache/fake`, as shown above.

This step hasn't been run yet, so expect to fix problems on the first deploy.

1. Create a Baseten API key that can deploy: a personal key, or a team key with **Manage and call all team models**. Other team keys fail on deploy with an authorization error.
2. Install Truss, log in, and deploy:

    ```bash
    .venv/bin/pip install truss
    ```

    ```bash
    cd baseten/audiogen
    ```

    ```bash
    ../../.venv/bin/truss login
    ```

    ```bash
    ../../.venv/bin/truss push
    ```

    The first deployment goes to production automatically. For later deployments, use `truss push --promote`.

3. Wait for the deployment to become active in the Baseten dashboard, then go back to the `audio` folder and set your credentials. The tool doesn't read `.env` files, so these must be exported in the same terminal:

    ```bash
    cd ../..
    ```

    ```bash
    export BASETEN_MODEL_ID=your_model_id
    ```

    ```bash
    export BASETEN_API_KEY=your_api_key
    ```

4. Run without `--fake`:

    ```bash
    .venv/bin/python -m musefx_audio outputs/sample.mp4 fixtures/sample_events.json -o outputs/real.mp4
    ```

**Expected last line on the first run:**

```text
wrote outputs/real.mp4: 9 events played, 1 skipped, 7 sounds generated, 0 from cache
```

The first run is slower because the GPU has to start. Listen using the table in step 3, and also check that each sound matches its prompt in `fixtures/sample_events.json`. For example, the 5.50 sound should be a bright coin chime.

Real sounds are saved in `.cache/sounds`. To generate them fresh after changing the Truss, clear that folder first:

```bash
rm -rf .cache/sounds
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
