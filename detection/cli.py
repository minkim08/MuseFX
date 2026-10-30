"""Thin CLI over the pipeline core for local runs and fixtures.

Usage: python -m detection.cli INPUT.mp4 OUTPUT.json [--id VIDEO_ID]
The API service (detection.service) is the integration contract; this is a dev tool.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .pipeline import detect


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="detection")
    p.add_argument("input", type=pathlib.Path)
    p.add_argument("output", type=pathlib.Path)
    p.add_argument("--id", default="clip")
    args = p.parse_args(argv)
    if not args.input.exists():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2
    result = detect(args.input, video_id=args.id,
                    progress=lambda s, pct: print(f"{s} {pct}%", file=sys.stderr))
    args.output.write_text(json.dumps(result, indent=2))
    print(f"wrote {args.output} ({len(result['events'])} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
