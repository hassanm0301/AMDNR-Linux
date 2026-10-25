#!/usr/bin/env python3
"""Compare benchmark runs shot by shot against a reference run.

  compare.py REF RUN [RUN ...]                         metrics table
  compare.py crops OUT.png SHOT X Y W H RUN [RUN ...]  side-by-side crops (GAIN=, ZOOM= env)

Runs are read from AMDNR_BENCH_WORK/runs (default linux/build/nr-quality/runs).

Shots are matched by name (s004.png ...). Each 128x128 patch of a run is aligned to
the reference by phase correlation (the captures are a few pixels apart), then:
  detail  = high-frequency energy (image minus Gaussian sigma 1.2) relative to the reference
  struct  = correlation of the sigma 1..3 band with the reference's (1 = same structure)
  smear%  = share of textured reference patches whose detail fell under 55%
  halo    = mean overshoot past the reference's local 9x9 range on strong reference edges (sigma 0.8)
  dE      = mean absolute colour change (0..255), the NR relight itself
  flick   = median change between consecutive frames of the bursts, after per-patch alignment
Two runs of one configuration differ by about 0.03 struct, 0.02 halo and 0.15 flick.
"""
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

WORK = Path(os.environ.get("AMDNR_BENCH_WORK", Path(__file__).resolve().parent.parent / "build/nr-quality"))
P = 128
# Leave out the HUD corners (fps counter top-left, Exit bottom-right).
MASK_BOXES = [(0, 0, 400, 220), (2600, 1300, 3440, 1440)]


def load(run, shot):
    path = WORK / "runs" / run / "shots" / shot
    if not path.exists():
        return None
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)


def luma(rgb):
    return rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def shift_of(a, b):
    fa, fb = np.fft.fft2(a - a.mean()), np.fft.fft2(b - b.mean())
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-6
    c = np.fft.ifft2(r).real
    y, x = np.unravel_index(np.argmax(c), c.shape)
    if y > a.shape[0] // 2:
        y -= a.shape[0]
    if x > a.shape[1] // 2:
        x -= a.shape[1]
    return int(y), int(x), float(c.max())


def metrics(ref_rgb, img_rgb):
    ref, img = luma(ref_rgb), luma(img_rgb)
    hf_ref = ref - ndimage.gaussian_filter(ref, 1.2)
    hf_img = img - ndimage.gaussian_filter(img, 1.2)
    # Mid band (sigma 1..3): survives the sub-pixel differences between two captures.
    band_ref = ndimage.gaussian_filter(ref, 1.0) - ndimage.gaussian_filter(ref, 3.0)
    band_img = ndimage.gaussian_filter(img, 1.0) - ndimage.gaussian_filter(img, 3.0)
    # Halo: overshoot past the reference's 9x9 range on its strongest edges, both lightly blurred.
    ref_b, img_b = ndimage.gaussian_filter(ref, 0.8), ndimage.gaussian_filter(img, 0.8)
    lo, hi = ndimage.minimum_filter(ref_b, 9), ndimage.maximum_filter(ref_b, 9)
    grad = np.hypot(ndimage.sobel(ref_b, 0), ndimage.sobel(ref_b, 1))
    H, W = ref.shape
    stats = {"detail": [], "struct": [], "smear": [], "halo_num": 0.0, "halo_den": 0, "dE": [], "textured": 0}
    m = 12
    for y in range(m, H - P - m, P):
        for x in range(m, W - P - m, P):
            if any(x < bx1 and x + P > bx0 and y < by1 and y + P > by0 for bx0, by0, bx1, by1 in MASK_BOXES):
                continue
            r = ref[y:y + P, x:x + P]
            if r.std() < 5:  # flat patch (sky, fog, black)
                continue
            dy, dx, peak = shift_of(r, img[y:y + P, x:x + P])
            if abs(dy) > m or abs(dx) > m:
                continue
            ys, xs = slice(y, y + P), slice(x, x + P)
            ys2, xs2 = slice(y - dy, y - dy + P), slice(x - dx, x - dx + P)
            e_ref = float((hf_ref[ys, xs] ** 2).mean())
            stats["textured"] += 1
            ratio = float((hf_img[ys2, xs2] ** 2).mean()) / max(e_ref, 1e-3)
            stats["detail"].append(ratio)
            stats["struct"].append(float(np.corrcoef(band_ref[ys, xs].ravel(), band_img[ys2, xs2].ravel())[0, 1]))
            stats["smear"].append(ratio < 0.55 and e_ref > 4.0)
            edge = grad[ys, xs] > np.percentile(grad[ys, xs], 95)
            # Take out the patch's overall brightness change (NR relights): only edge overshoot remains.
            ib = img_b[ys2, xs2] - (img_b[ys2, xs2].mean() - ref_b[ys, xs].mean())
            over = np.maximum(ib - hi[ys, xs], 0) + np.maximum(lo[ys, xs] - ib, 0)
            stats["halo_num"] += float(over[edge].sum())
            stats["halo_den"] += int(edge.sum())
            stats["dE"].append(float(np.abs(img_rgb[ys2, xs2] - ref_rgb[ys, xs]).mean()))
    if not stats["detail"]:
        return None
    return {
        "detail": float(np.median(stats["detail"])),
        "struct": float(np.median(stats["struct"])),
        "smear": 100.0 * float(np.mean(stats["smear"])),
        "halo": stats["halo_num"] / max(stats["halo_den"], 1),
        "dE": float(np.mean(stats["dE"])),
        "n": stats["textured"],
    }


def flicker(run, burst):
    """Median motion-compensated change between consecutive burst frames (luma 0..255)."""
    frames = sorted((WORK / "runs" / run / "shots").glob(f"{burst}_*.png"))
    if len(frames) < 3:
        return None
    lum = [ndimage.gaussian_filter(luma(np.asarray(Image.open(f).convert("RGB"), dtype=np.float32)), 0.7)
           for f in frames]
    out = []
    for a, b in zip(lum, lum[1:]):
        H, W = a.shape
        vals = []
        for y in range(16, H - P - 16, P):
            for x in range(16, W - P - 16, P):
                if any(x < bx1 and x + P > bx0 and y < by1 and y + P > by0 for bx0, by0, bx1, by1 in MASK_BOXES):
                    continue
                pa = a[y:y + P, x:x + P]
                if pa.std() < 6:
                    continue
                dy, dx, _ = shift_of(pa, b[y:y + P, x:x + P])
                if abs(dy) > 16 or abs(dx) > 16:
                    continue
                vals.append(float(np.abs(pa - b[y - dy:y - dy + P, x - dx:x - dx + P]).mean()))
        if vals:
            out.append(float(np.median(vals)))
    return float(np.mean(out)) if out else None


def table(ref, runs):
    shots = sorted(p.name for p in (WORK / "runs" / ref / "shots").glob("s*.png"))
    print(f"reference {ref}; {len(shots)} shots")
    bursts = sorted({p.name.split("_")[0] for p in (WORK / "runs" / ref / "shots").glob("b*_0.png")})
    print(f"{'run':28} {'detail':>7} {'struct':>7} {'smear%':>7} {'halo':>6} {'dE':>6} {'flick':>6}  shots")
    for run in [ref]:
        fl = [f for f in (flicker(run, b) for b in bursts) if f is not None]
        print(f"{run:28} {'':7} {'':7} {'':7} {'':6} {'':6} {np.mean(fl) if fl else float('nan'):6.2f}  (reference)")
    for run in runs:
        rows = []
        for shot in shots:
            a, b = load(ref, shot), load(run, shot)
            if a is None or b is None:
                continue
            r = metrics(a, b)
            if r:
                rows.append(r)
        if not rows:
            print(f"{run:28} no matching shots")
            continue
        avg = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
        fl = [f for f in (flicker(run, b) for b in bursts) if f is not None]
        fl = np.mean(fl) if fl else float("nan")
        print(f"{run:28} {avg['detail']:7.3f} {avg['struct']:7.3f} {avg['smear']:7.1f} {avg['halo']:6.2f} {avg['dE']:6.2f} {fl:6.2f}  {len(rows)}")


def crops(out, shot, x, y, w, h, runs):
    tiles = []
    for run in runs:
        im = Image.open(WORK / "runs" / run / "shots" / shot).convert("RGB").crop((x, y, x + w, y + h))
        gain = float(os.environ.get("GAIN", "1"))
        if gain != 1:
            im = Image.fromarray(np.clip(np.asarray(im, dtype=np.float32) * gain, 0, 255).astype(np.uint8))
        zoom = int(os.environ.get("ZOOM", "1"))
        if zoom > 1:
            im = im.resize((w * zoom, h * zoom), Image.NEAREST)
        tiles.append((run, im))
    from PIL import ImageDraw
    w, h = tiles[0][1].size
    canvas = Image.new("RGB", (w * len(tiles) + 4 * (len(tiles) - 1), h + 22), (40, 40, 40))
    d = ImageDraw.Draw(canvas)
    for i, (run, im) in enumerate(tiles):
        canvas.paste(im, (i * (w + 4), 22))
        d.text((i * (w + 4) + 4, 4), run, fill=(255, 255, 0))
    canvas.save(out)


if __name__ == "__main__":
    if sys.argv[1] == "crops":
        out, shot, x, y, w, h = sys.argv[2:8]
        crops(out, shot, int(x), int(y), int(w), int(h), sys.argv[8:])
    else:
        table(sys.argv[1], sys.argv[2:])
