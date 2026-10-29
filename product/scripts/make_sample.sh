#!/usr/bin/env bash
set -euo pipefail
# Generate a valid, silent H.264 MP4 for manual end-to-end testing.
ffmpeg -y -f lavfi -i 'testsrc2=size=640x360:rate=30:duration=4' \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart "${1:-sample.mp4}"
