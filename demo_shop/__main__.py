"""Run the demo shop: `uv run python -m demo_shop [--bugs checkout-500,wrong-total | all] [--variant redesign]`."""

from __future__ import annotations

import argparse

from .server import BUGS, DEFAULT_PORT, VARIANTS, make_server, parse_names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo_shop", description="Kulhad & Co., a demo shop with planted bugs.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bugs", default="", help='comma-separated bug names, or "all"')
    parser.add_argument("--variant", default="", help="comma-separated UI variants (legitimate changes, not bugs)")
    parser.add_argument("--list-bugs", action="store_true", help="list the planted bugs and variants, then exit")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)

    if args.list_bugs:
        for name, bug in BUGS.items():
            print(f"{name:27} {bug.description}  (caught by: {', '.join(bug.specs)})")
        print()
        for name, description in VARIANTS.items():
            print(f"variant {name:19} {description}")
        return 0

    try:
        bugs = parse_names(args.bugs, BUGS)
        variants = parse_names(args.variant, VARIANTS)
    except ValueError as exc:
        parser.error(f"{exc}; see --list-bugs")
    server = make_server(port=args.port, bugs=bugs, variants=variants, verbose=args.verbose)
    print(f"Kulhad & Co. on http://localhost:{server.server_port}")
    print(f"   bugs on: {', '.join(sorted(bugs)) or 'none'}   variants: {', '.join(sorted(variants)) or 'none'}")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
