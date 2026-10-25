#!/usr/bin/env python3
"""Run the Black Myth: Wukong Benchmark Tool unattended and capture comparable frames.

  wkbench.py baseline
  wkbench.py run NAME [--set Section.Key=value ...] [--game-setting Key=value ...]
                      [--ref RUN] [--bursts 30,100] [--times 4,12,...]

The benchmark runs inside a nested KWin on its own D-Bus session (it never touches the
desktop's), through the AMDNR wrapper already installed in the game. Every run starts
from the OptiScaler.ini and GameUserSettings.ini saved by "baseline" and applies its
own edits. Frames come from KWin's ScreenShot2 interface (grab.py) at fixed times after
the benchmark sequence starts. With --ref, each target keeps the frame whose camera pose
best matches that run's frame, so runs can be compared pixel region by pixel region.

Paths come from the environment:
  AMDNR_BENCH_GAME    the "Black Myth Wukong Benchmark Tool" folder (required)
  AMDNR_BENCH_PROTON  the Proton folder the game's prefix uses (required)
  AMDNR_BENCH_WORK    where runs are kept (default: linux/build/nr-quality)
The menu templates and click positions are for a 3440x1440 output.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
WIDTH, HEIGHT = 3440, 1440
APP_ID = "3132990"
DEFAULT_TIMES = [4, 12, 20, 28, 36, 44, 52, 60, 68, 76, 84, 92, 100, 108, 116, 124]
LOGS = ["lmxxf_backend.log", "amd_bridge.log", "OptiScaler.log", "amd_presr.log", "dlssnr_on_amd.log"]
# Positions on a 3440x1440 output: the menu's Benchmark entry, the dialog's Confirm button,
# and where each template sits on screen.
BENCHMARK_ITEM, CONFIRM_BUTTON = (203, 647), (1445, 846)
TEMPLATE_BOXES = {"menu": (110, 625), "confirm": (1400, 640), "results": (525, 320)}


class Paths:
    def __init__(self):
        missing = [v for v in ("AMDNR_BENCH_GAME", "AMDNR_BENCH_PROTON") if not os.environ.get(v)]
        if missing:
            raise SystemExit("Set " + " and ".join(missing))
        self.game = Path(os.environ["AMDNR_BENCH_GAME"])
        self.win64 = self.game / "b1/Binaries/Win64"
        self.config = self.game / "b1/Saved/Config/Windows/GameUserSettings.ini"
        self.compat = self.game.parents[1] / "compatdata" / APP_ID
        self.proton = Path(os.environ["AMDNR_BENCH_PROTON"])
        self.work = Path(os.environ.get("AMDNR_BENCH_WORK", HERE.parent / "build/nr-quality"))
        if not (self.win64 / "amdnr-linux/run.sh").is_file():
            raise SystemExit(f"AMDNR is not installed in {self.win64}")


def edit_ini(text, section, key, value):
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip() == f"[{section}]"), None)
    if start is None:
        raise SystemExit(f"No section [{section}]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    for i in range(start + 1, end):
        if re.match(rf";?{re.escape(key)}\s*=", lines[i]):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.insert(start + 1, f"{key}={value}")
    return "\n".join(lines)


def baseline(paths):
    base = paths.work / "base"
    base.mkdir(parents=True, exist_ok=True)
    shutil.copy2(paths.win64 / "OptiScaler.ini", base / "OptiScaler.ini")
    shutil.copy2(paths.config, base / "GameUserSettings.ini")
    print(f"Saved the installed settings to {base}")


def prepare(paths, args):
    base = paths.work / "base"
    text = (base / "OptiScaler.ini").read_text(encoding="utf-8", errors="surrogateescape")
    for item in args.set:
        target, value = item.split("=", 1)
        section, key = target.split(".", 1)
        text = edit_ini(text, section, key, value)
    (paths.win64 / "OptiScaler.ini").write_text(text, encoding="utf-8", errors="surrogateescape")
    game = (base / "GameUserSettings.ini").read_text()
    for item in args.game_setting:
        key, value = item.split("=", 1)
        game, count = re.subn(rf"^{re.escape(key)}=.*$", f"{key}={value}", game, flags=re.M)
        if not count:
            raise SystemExit(f"No game setting {key}")
    paths.config.write_text(game)
    for name in LOGS:
        (paths.win64 / name).unlink(missing_ok=True)


def kwin_environment(paths):
    """A private config, data and cache home for the nested KWin only.

    The config stops KWin asking before xdotool may send input; the desktop file lets
    this Python take KWin screenshots (KWin authorises an executable by its desktop file).
    """
    root = paths.work / "kwin"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data/applications").mkdir(parents=True, exist_ok=True)
    (root / "cache").mkdir(parents=True, exist_ok=True)
    (root / "config/kwinrc").write_text("[Xwayland]\nXwaylandEisNoPrompt=true\n")
    (root / "data/applications/amdnr-bench-grab.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=AMDNR benchmark capture\nNoDisplay=true\n"
        f"Exec={Path(sys.executable).resolve()}\nX-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2\n")
    return dict(os.environ, XDG_CONFIG_HOME=str(root / "config"), XDG_DATA_HOME=str(root / "data"),
                XDG_CACHE_HOME=str(root / "cache"))


def run(paths, args):
    run_dir = paths.work / "runs" / args.name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / "shots").mkdir(parents=True)
    json.dump(vars(args), open(run_dir / "config.json", "w"), indent=1)
    prepare(paths, args)
    inner = f"{Path(sys.executable).resolve()} {Path(__file__).resolve()} _inner {run_dir}"
    started = time.time()
    with open(run_dir / "kwin.log", "w") as log:
        try:
            subprocess.run(["dbus-run-session", "kwin_wayland", "--virtual", "--xwayland",
                            "--socket", f"amdnr-bench-{os.getpid()}", "--width", str(WIDTH), "--height", str(HEIGHT),
                            "--no-lockscreen", "--no-global-shortcuts", "--exit-with-session", inner],
                           env=kwin_environment(paths), stdout=log, stderr=log, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            print("Timed out")
    stop_game(paths)
    for name in LOGS:
        if (paths.win64 / name).exists():
            shutil.copy2(paths.win64 / name, run_dir / name)
    status = (run_dir / "status.txt").read_text().strip() if (run_dir / "status.txt").exists() else "no status"
    print(f"{args.name}: {status} ({time.time() - started:.0f} s)")


def stop_game(paths):
    subprocess.run(["pkill", "-f", "b1-Win64-Shipping"], check=False)
    time.sleep(2)
    subprocess.run([str(paths.proton / "files/bin/wineserver"), "-k"],
                   env=dict(os.environ, WINEPREFIX=str(paths.compat / "pfx")), check=False, timeout=30)


class Screen:
    def __init__(self, run_dir):
        import numpy
        from PIL import Image
        sys.path.insert(0, str(HERE))
        from grab import Grabber
        self.np, self.Image = numpy, Image
        self.grabber = Grabber()
        self.templates = {name: self.gray(numpy.asarray(Image.open(HERE / f"templates/{name}.png").convert("RGB")))
                          for name in TEMPLATE_BOXES}
        self.run_dir = run_dir
        self.savers = []

    def gray(self, a):
        return a.astype(self.np.float32) @ self.np.array([0.2126, 0.7152, 0.0722], dtype=self.np.float32)

    def grab(self):
        try:
            return self.grabber.grab()
        except Exception:
            return None

    def shows(self, im, name):
        t = self.templates[name]
        x, y = TEMPLATE_BOXES[name]
        crop = self.gray(im[y:y + t.shape[0], x:x + t.shape[1]])
        a, b = crop - crop.mean(), t - t.mean()
        return float((a * b).sum() / (self.np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6)) > 0.85

    def mean(self, im):
        return float(self.gray(im[::10, ::10]).mean())

    def signature(self, im):
        """Camera-pose signature that ignores tone: high-passed 1/8 luminance, normalised."""
        from scipy import ndimage
        g = self.gray(im[::8, ::8])
        g = g - ndimage.gaussian_filter(g, 3)
        return (g - g.mean()) / (g.std() + 1e-6)

    def save(self, im, name):
        def work():
            self.Image.fromarray(self.np.ascontiguousarray(im)).save(self.run_dir / "shots" / name, compress_level=1)
        thread = threading.Thread(target=work)
        thread.start()
        self.savers.append(thread)


def inner(run_dir):
    """Runs inside the nested session: start the game, drive its menu, capture frames."""
    import numpy as np
    run_dir = Path(run_dir)
    cfg = json.load(open(run_dir / "config.json"))
    paths = Paths()
    for var, default in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
        os.environ[var] = str(Path.home() / default)
    env = dict(os.environ, STEAM_COMPAT_DATA_PATH=str(paths.compat),
               STEAM_COMPAT_CLIENT_INSTALL_PATH=str(Path.home() / ".local/share/Steam"),
               STEAM_COMPAT_INSTALL_PATH=str(paths.game), SteamAppId=APP_ID, SteamGameId=APP_ID)
    game = subprocess.Popen([str(paths.win64 / "amdnr-linux/run.sh"), str(paths.proton / "proton"), "waitforexitandrun",
                             str(paths.game / "b1_benchmark.exe")], cwd=paths.game, env=env,
                            stdout=open(run_dir / "game.log", "w"), stderr=subprocess.STDOUT)
    screen = Screen(run_dir)
    events = open(run_dir / "events.txt", "w")
    launched = time.monotonic()

    def note(text):
        events.write(f"{time.monotonic() - launched:7.1f} {text}\n")
        events.flush()

    def xdotool(*a):
        subprocess.run(["xdotool", *a], check=False)

    def click(position):
        xdotool("mousemove", str(position[0]), str(position[1]))
        time.sleep(0.3)
        xdotool("click", "1")

    reference = None
    if cfg["ref"]:
        data = np.load(paths.work / "runs" / cfg["ref"] / "signatures.npz")
        reference = {k: data[k] for k in data.files}
    times = [float(t) for t in cfg["times"].split(",")] if cfg["times"] else list(DEFAULT_TIMES)
    bursts = [float(t) for t in cfg["bursts"].split(",") if t]
    window = 1.2 if reference else 0.5
    best, signatures = {}, {}
    burst = None
    status, state = "failed", "title"
    start = None
    black_seen = False
    last_input = 0
    try:
        while game.poll() is None:
            now = time.monotonic()
            if now - launched > cfg["timeout"] - 60 or (state == "title" and now - launched > 240):
                note(f"gave up in state {state}")
                break
            im = screen.grab()
            if im is None:
                time.sleep(0.2)
                continue
            if state == "title":
                if screen.shows(im, "menu"):
                    note("menu")
                    time.sleep(1)
                    click(BENCHMARK_ITEM)
                    state = "confirm"
                elif now - last_input > 4 and screen.mean(im) > 3:
                    xdotool("key", "Return")
                    last_input = now
                time.sleep(0.3)
                continue
            if state == "confirm":
                if screen.shows(im, "confirm"):
                    note("confirm")
                    click(CONFIRM_BUTTON)
                    state = "loading"
                elif now - last_input > 8:
                    click(BENCHMARK_ITEM)
                    last_input = now
                time.sleep(0.3)
                continue
            if state == "loading":
                level = screen.mean(im)
                black_seen = black_seen or level < 2
                if not (black_seen and level > 6):
                    time.sleep(0.01)
                    continue
                start = now
                note(f"sequence start, mean {level:.1f}")
                state = "sequence"
            at = now - start
            for target in times:
                if abs(at - target) > window:
                    continue
                key = f"s{int(target):03d}"
                if reference is not None and key in reference:
                    score = float((screen.signature(im) * reference[key]).mean())
                else:
                    score = -abs(at - target)
                if key not in best or score > best[key][0]:
                    best[key] = (score, im, at)
            for key in [k for k in best if at - int(k[1:]) > window]:
                score, frame, taken = best.pop(key)
                screen.save(frame, key + ".png")
                signatures[key] = screen.signature(frame)
                note(f"shot {key} at {taken:.3f} score {score:.3f}")
                times.remove(float(int(key[1:])))
            if burst is None and bursts and at >= bursts[0]:
                burst = (bursts.pop(0), [])
            if burst is not None:
                frames = burst[1]
                if not frames or not np.array_equal(frames[-1][::64, ::64], im[::64, ::64]):
                    frames.append(im)
                if len(frames) >= 8:
                    for i, frame in enumerate(frames):
                        screen.save(frame, f"b{int(burst[0]):03d}_{i}.png")
                    note(f"burst b{int(burst[0]):03d} done at {at:.3f}")
                    burst = None
            if screen.shows(im, "results"):
                note(f"results at {at:.1f}")
                screen.save(im, "results.png")
                status = "ok"
                break
            time.sleep(0.005 if (burst is not None or best) else 0.05)
    finally:
        for thread in screen.savers:
            thread.join()
        if signatures:
            np.savez_compressed(run_dir / "signatures.npz", **signatures)
        (run_dir / "status.txt").write_text(status + "\n")
        subprocess.run(["pkill", "-f", "b1-Win64-Shipping"], check=False)
        try:
            game.wait(timeout=30)
        except subprocess.TimeoutExpired:
            game.kill()


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "_inner":
        return inner(sys.argv[2])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("baseline", help="save the installed OptiScaler.ini and game settings as every run's start")
    one = commands.add_parser("run", help="run the benchmark once")
    one.add_argument("name")
    one.add_argument("--set", action="append", default=[], help="Section.Key=value in OptiScaler.ini")
    one.add_argument("--game-setting", action="append", default=[], help="Key=value in GameUserSettings.ini")
    one.add_argument("--ref", default="", help="run whose frames define the camera poses to match")
    one.add_argument("--times", default="", help="capture times in seconds after the sequence starts")
    one.add_argument("--bursts", default="", help="times of 8-frame bursts of consecutive frames")
    one.add_argument("--timeout", type=int, default=720)
    args = parser.parse_args()
    paths = Paths()
    if args.command == "baseline":
        baseline(paths)
    else:
        run(paths, args)


if __name__ == "__main__":
    main()
