# MuseFX

AI pipeline that detects key actions in silent gameplay footage and generates perfectly synced sound effects.

## Local Product MVP

See [Product setup and validation](product/README.md) for the Next.js frontend and Rust/Axum backend. This iteration uses mock Detection/Audio adapters with FFmpeg and follows the shared `events.json` contract. No real models or Modal deployment are included yet.

## Licence

The MIT licence in [LICENSE](LICENSE) covers MuseFX's own code only. It does not cover third-party models, weights, APIs, or tools (such as AudioGen/AudioCraft, SAM 3.1, Muse Spark via the Meta Model API, and ffmpeg), or any game footage or other video content, which remain under their own terms. No model weights are distributed in this repository.

AudioGen weights are licensed for non-commercial use only; MuseFX is a non-commercial hackathon project and uses them on that basis.
