#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Start the independent native Linux manager."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--steam-root", type=Path, action="append")
    parser.add_argument("--snapshot", type=Path, help="Render the library, save an image, then exit (read-only)")
    parser.add_argument("--diagnose", action="store_true", help="List detected games as JSON without opening the GUI")
    args = parser.parse_args()
    from native_launcher.controller import Controller
    controller = Controller(args.release_root, args.data_dir, args.cache_dir, args.steam_root)
    if args.diagnose:
        games = controller.discover()
        print(json.dumps([{"name": g.name, "appid": g.appid, "directory": str(g.install_dir),
                           "executable_directory": str(g.exe_dir) if g.exe_dir else None} for g in games], indent=2))
        return
    from native_launcher.app import run
    run(controller, snapshot=args.snapshot)


if __name__ == "__main__":
    main()
