# Testing MuseFX end to end

How to run the whole thing on your own machine and check each part: Detection, Audio and the website. Run every command from the root of your MuseFX clone unless a step says `cd`.

There are three runs, from free to fully real:

| Run | Detection | Sounds | Costs |
|---|---|---|---|
| A | Mock (one event at 1 s) | Fake synthetic sounds | Nothing |
| B | Real (Meta Model API) | Fake synthetic sounds | Meta API credits |
| C | Real | Real AudioGen on Runpod | Meta API credits + Runpod ($0.75/hr while a GPU runs) |

Do A first. If A works, any problem in B or C is in Detection or Runpod, not the website.

## 1. Get the code

Everything is on `main`:

```bash
git switch main
```

```bash
git pull
```

## 2. Install the tools (once)

You need Rust (recent stable), Node.js 22 or newer, Python 3.10 or newer, and ffmpeg. On macOS:

```bash
brew install node python ffmpeg
```

Install Rust from https://rustup.rs. If you already have Rust, update it. Rust 1.82 is too old and fails with an `edition2024` error:

```bash
rustup update stable
```

## 3. Set up each part (once)

Detection, from the repository root:

```bash
python3 -m venv .venv && .venv/bin/pip install -r detection/requirements.txt
```

Audio:

```bash
cd audio && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cd ..
```

Website:

```bash
cd product/frontend && npm install && cd ../..
```

## 4. Keys

Never commit keys or paste them in chat. `.env` files are ignored by git.

**Meta Model API key** (runs B and C). Copy the example file, then put your key after `MODEL_API_KEY=`:

```bash
cp detection/.env.example detection/.env
```

```bash
open -e detection/.env
```

**Runpod API key** (run C only). Ask Simon for it, or create one in the Runpod console under Settings → API Keys. The AudioGen endpoint is already deployed: `MuseFX-audio`, id `0hx4r90cwocl3a`. You don't need to deploy anything.

## 5. Run the automated tests

Each should pass with no failures.

```bash
.venv/bin/python -m pytest detection/tests
```

```bash
cd audio && .venv/bin/python -m pytest && cd ..
```

Expected: 34 passed.

```bash
cd product/backend && cargo test && cd ../..
```

```bash
cd product/frontend && npm run typecheck && npm run build && cd ../..
```

## 6. Get a test clip

The site takes `.mp4` only, up to 60 seconds, 1080p and 100 MB. Use a short gameplay clip with clear action: footsteps, hits, doors. A `.mov` from a Mac screen recording needs converting first:

```bash
ffmpeg -i ~/Downloads/clip.mov -c copy -movflags +faststart ~/Downloads/clip.mp4
```

If that fails, re-encode instead (slower):

```bash
ffmpeg -i ~/Downloads/clip.mov -c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart ~/Downloads/clip.mp4
```

For a quick 4-second silent clip with nothing happening in it (good for run A only):

```bash
bash product/scripts/make_sample.sh
```

## 7. Run A: the website with mock Detection and fake sounds

Terminal 1, backend:

```bash
cd product/backend
```

```bash
AUDIO_DIR=../../audio AUDIO_FAKE=1 cargo run
```

Wait for it to say it's listening on 127.0.0.1:8000. Terminal 2, website:

```bash
cd product/frontend
```

```bash
npm run dev
```

Open http://localhost:3000 and upload your clip.

**Expected:** the stages run to "done" in under a minute. The result plays your clip with one short synthetic sound at about 1 second, and one marker on the timeline. The picture looks exactly like the original. Any audio the clip already had is still there, under the new sound. The MP4 download works.

## 8. Run B: real Detection, fake sounds

Terminal 3, Detection, from the repository root:

```bash
.venv/bin/python -m uvicorn detection.service:app --host 127.0.0.1 --port 8100
```

Check it in another terminal:

```bash
curl http://localhost:8100/health
```

It should show `"model_api_key_configured":true`. If it says `false`, fix `detection/.env` and restart Detection.

Stop the backend from run A (Ctrl+C) and start it with Detection wired in:

```bash
DETECTION_API_URL=http://127.0.0.1:8100 AUDIO_DIR=../../audio AUDIO_FAKE=1 cargo run
```

Upload the clip again.

**Expected:** Detection takes longer, up to a few minutes for a long clip. The timeline shows markers where things happen in the clip, and you hear a synthetic sound at each one. Check that the markers line up with the action. Download `events.json` if you want to see what Detection found.

## 9. Run C: everything real

Stop the backend (Ctrl+C) and start it with the Runpod key. Leave Detection and the website running.

```bash
export RUNPOD_ENDPOINT_ID=0hx4r90cwocl3a
```

```bash
export RUNPOD_API_KEY=paste_the_key_here
```

```bash
DETECTION_API_URL=http://127.0.0.1:8100 AUDIO_DIR=../../audio cargo run
```

Upload the clip.

**Expected:** the first run after a while idle has a cold start. A GPU worker has to start and load AudioGen, which can take 5 minutes or more. The page shows "Mixing" for the whole wait; that's normal, not a hang. Later runs within a few minutes are faster. Sounds already made for the same prompt are reused from `audio/.cache`, so a repeat upload of the same clip is quick and free.

You can watch the job in the Runpod console: Serverless → `MuseFX-audio` → Requests.

**Listen for:**
- Each sound starts on its action, not early or late
- Sounds match what they're for (a footstep sounds like a footstep)
- Repeated sounds vary a little instead of being identical
- Sounds pan left and right with where the action is on screen
- No clicks, pops or distortion, and nothing much louder than the rest

## 10. If something goes wrong

| What you see | Fix |
|---|---|
| `cargo run` fails with `edition2024` | `rustup update stable` |
| Backend won't start: `.../audio/.venv/bin/python is missing` | Do the Audio setup in step 3 |
| Upload rejected | It must be `.mp4`, up to 60 s, 1080p and 100 MB |
| Detection health shows `model_api_key_configured: false` | Put the key in `detection/.env`, restart Detection |
| Job fails: `Set RUNPOD_ENDPOINT_ID and RUNPOD_API_KEY` | Export both in the same terminal as `cargo run` |
| Job fails: `Runpod returned HTTP 401` | Wrong Runpod key |
| Job fails: `was not done after 1800 s` | Runpod couldn't get a GPU. Check the endpoint's Workers tab, then try again |
| Run A gives a single beep instead of a sound | `AUDIO_DIR` isn't set, so it used the mock test tone |

If a job fails, the reason shows on the page. Copy it along with the last lines of the backend terminal and send them to the team.

More detail: [README.md](README.md), [detection/README.md](detection/README.md), [audio/TESTING.md](audio/TESTING.md), [product/README.md](product/README.md).
