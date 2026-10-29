# MuseFX

AI pipeline that detects key actions in silent gameplay footage and generates perfectly synced sound effects.

## Local Product MVP

See [Product setup and validation](product/README.md) for the Next.js frontend and Rust/Axum backend. This iteration uses mock Detection/Audio adapters with FFmpeg and follows the shared `events.json` contract. No real models or Modal deployment are included yet.

## Licence

The MIT licence in [LICENSE](LICENSE) covers MuseFX's own code only. It does not cover third-party models, weights, APIs, or tools (such as AudioGen/AudioCraft, SAM 3.1, Muse Spark via the Meta Model API, and ffmpeg), or any game footage or other video content, which remain under their own terms. No model weights are distributed in this repository.

AudioGen weights are licensed for non-commercial use only; MuseFX is a non-commercial hackathon project and uses them on that basis.

To run it on local dev

### 1. Install prerequisites
brew install rust node@22 ffmpeg
Ensure Homebrew tools are available:
export PATH="/opt/homebrew/bin:$PATH"
Verify:

cargo --version
node --version
ffmpeg -version
ffprobe -version

### 2. Start the Rust backend

cd MuseFX/product/backend
cargo run

Leave that terminal running. The backend should start at:

http://localhost:8000

Check it from another terminal:

curl http://localhost:8000/health

Both dependencies should show true.

### 3. Start the frontend

Open another terminal:

export PATH="/opt/homebrew/bin:$PATH"

cd MuseFX/product/frontend
npm install
npm run dev

Open:

http://localhost:3000

### 4. Generate a sample video

From the repository root:

cd MuseFX
bash product/scripts/make_sample.sh

This creates sample.mp4. Upload it through the web interface. The mock pipeline should produce a result containing a short test tone at one second.

This local version uses mock Detection and Audio adapters; it does not call the real Meta models yet.
