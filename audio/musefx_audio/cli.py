"""Command line: python -m musefx_audio VIDEO EVENTS -o OUT [--fake]"""

import argparse
import sys

from .generators import BasetenGenerator, FakeGenerator
from .render import DEFAULT_CACHE_DIR, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="musefx_audio", description="Add synced sound effects to a video.")
    parser.add_argument("video", help="input MP4")
    parser.add_argument("events", help="events.json from Detection")
    parser.add_argument("-o", "--out", required=True, help="output MP4")
    parser.add_argument("--fake", action="store_true", help="use synthetic sounds instead of AudioGen on Baseten")
    parser.add_argument(
        "--environment", default="production",
        help="Baseten environment: production (default) or development",
    )
    parser.add_argument("--candidates", type=int, default=3, help="options generated per sound (default 3)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    args = parser.parse_args(argv)

    try:
        generator = FakeGenerator() if args.fake else BasetenGenerator(environment=args.environment)
        result = render(
            args.video, args.events, args.out, generator,
            cache_dir=args.cache_dir, n_candidates=args.candidates, seed=args.seed,
            on_progress=lambda stage, fraction: print(f"{stage} {fraction:.0%}", file=sys.stderr),
        )
    except (RuntimeError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(
        f"wrote {result.out_path}: {result.events_played} events played, "
        f"{result.events_skipped} skipped, {result.sounds_generated} sounds generated, "
        f"{result.sounds_from_cache} from cache"
    )
    return 0
