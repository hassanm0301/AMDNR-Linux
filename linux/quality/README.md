# Neural-rendering image-quality bench

Runs the free **Black Myth: Wukong Benchmark Tool** (Steam app 3132990) unattended with
AMDNR installed, captures frames at fixed points of its camera path, and compares runs
against a reference run with neural rendering off. It is how settings and builds are
judged on the picture rather than by eye alone.

## Requirements

- The benchmark installed through Steam, started once by hand (first-run shader compile
  and the privacy dialog), and AMDNR installed in `b1/Binaries/Win64`.
- Steam running (the benchmark checks its licence through it).
- KDE's `kwin_wayland`, `dbus-run-session`, `xdotool`, Python 3 with `numpy`, `scipy`,
  `Pillow` and `dbus-python`.
- A 3440x1440 nested output: the menu templates and click positions are for that size.

Each run starts a nested `kwin_wayland --virtual` on its own D-Bus session, so the
desktop's session, shortcuts and windows are not touched; it works with the monitor off.
The nested KWin gets a private config, data and cache folder under the work folder: the
config lets `xdotool` drive the game without KWin's input-permission prompt, and a desktop
file there authorises the capturing Python for KWin's screenshot interface.

## Use

```bash
export AMDNR_BENCH_GAME="/path/to/steamapps/common/Black Myth Wukong Benchmark Tool"
export AMDNR_BENCH_PROTON=/path/to/the/Proton/the/prefix/uses
python3 linux/quality/wkbench.py baseline
python3 linux/quality/wkbench.py run ref-off --set DlssNr.Enabled=false --bursts 30,100
python3 linux/quality/wkbench.py run nr --bursts 30,100 --ref ref-off
python3 linux/quality/compare.py ref-off nr
```

`baseline` saves the installed `OptiScaler.ini` and the game's settings; every run starts
from them and applies its own `--set Section.Key=value` and `--game-setting Key=value`.
A run takes about 3 minutes. Results go to `linux/build/nr-quality/runs/NAME`: the frames
(`shots/s004.png` ... 4 to 124 s into the sequence, `b030_*`/`b100_*` bursts of eight
consecutive frames), the AMDNR logs and `results.png` with the benchmark's average fps.

With `--ref`, every capture keeps the frame whose camera pose best matches the reference
run's frame at that time (captures take about 15 ms, so consecutive frames are candidates),
which is what makes region-by-region comparison possible.

`queue.sh FILE` runs one line of FILE after another (`NAME ARGS...`), re-reading the file
after each run so experiments can be added while it works; `!NAME COMMAND` lines run a
command once, for example reinstalling another package and running `wkbench.py baseline`.

## Measures

`compare.py REF RUN...` aligns every 128x128 patch of each frame to the reference's and
prints, per run:

| column | meaning |
| --- | --- |
| detail | high-frequency energy relative to the reference (1 = as sharp) |
| struct | correlation of the 1-3 px band with the reference (1 = the same structure) |
| smear% | textured patches that lost more than 45% of their detail |
| halo | overshoot past the reference's local range on its strongest edges, after taking out each patch's brightness change |
| dE | mean colour change, the relight itself |
| flick | change between consecutive burst frames after alignment |

Two runs of the same configuration differ by about 0.03 struct, 0.03 halo and 0.1 flick.
`compare.py crops OUT.png SHOT X Y W H RUN...` puts the same region of several runs side
by side (`GAIN=1.6` brightens, `ZOOM=2` enlarges).
