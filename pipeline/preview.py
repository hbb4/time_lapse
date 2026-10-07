#!/usr/bin/env python3
"""
Quick preview video from the proxy frames, for checking a stretch of time before
a full-resolution render. Optionally applies a simple deflicker so its effect can
be judged side by side.

Usage:
  python3 -m pipeline.preview --start "2025-10-15 05:30" --end "2025-10-15 20:00"
  python3 -m pipeline.preview --start "2025-10-15 05:30" --end "2025-10-15 20:00" --deflicker
"""

import argparse
import io
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from . import config
from .analyze import open_db


def smooth_gain(luma, window):
    """Per-frame gain that pulls each frame's brightness onto a smoothed curve.

    A rolling median removes single-frame flicker, then a Gaussian smooths what is
    left. Working in log space keeps dusk and dawn moving, just without the jumps.
    """
    x = np.log(np.maximum(np.asarray(luma, dtype=np.float64), 1e-4))
    half = window // 2
    padded = np.pad(x, half, mode="edge")
    med = np.array([np.median(padded[i:i + window]) for i in range(len(x))])
    k = np.exp(-0.5 * (np.arange(-2 * half, 2 * half + 1) / max(half, 1)) ** 2)
    k /= k.sum()
    smooth = np.convolve(np.pad(med, 2 * half, mode="edge"), k, mode="valid")
    return np.exp(smooth - x)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help='local time, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--end", required=True)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--step", type=int, default=1, help="use every Nth frame")
    ap.add_argument("--deflicker", action="store_true")
    ap.add_argument("--window", type=int, default=9, help="deflicker window in frames")
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    t0 = datetime.fromisoformat(args.start).replace(tzinfo=config.TZ).timestamp()
    t1 = datetime.fromisoformat(args.end).replace(tzinfo=config.TZ).timestamp()
    db = open_db()
    # LEFT JOIN: a --limit test run writes frames and a proxy tar but no folders row
    rows = db.execute("SELECT f.folder, f.n, f.luma, d.proxy_path FROM frames f "
                      "LEFT JOIN folders d ON d.folder = f.folder "
                      "WHERE f.t_utc BETWEEN ? AND ? AND f.error IS NULL ORDER BY f.t_utc",
                      (t0, t1)).fetchall()[::args.step]
    rows = [(f, n, luma, path or str(config.PROXY_DIR / f"{f}.tar")) for f, n, luma, path in rows]
    if not rows:
        raise SystemExit("no analysed frames in that window (run pipeline.analyze first)")
    gains = smooth_gain([r[2] for r in rows], args.window) if args.deflicker else None

    tag = "deflicker" if args.deflicker else "raw"
    out = args.output or config.PREVIEW_DIR / (
        f"{args.start.replace(' ', '_').replace(':', '')}_{args.end.replace(' ', '_').replace(':', '')}_{tag}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)

    first = Image.open(io.BytesIO(next(iter(_frames(rows[:1])))))
    w, h = first.size
    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-framerate", str(args.fps), "-i", "-",
         "-vf", f"scale={w * 2}:{h * 2}:flags=lanczos", "-c:v", "libx264", "-crf", "20",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)
    for i, data in enumerate(_frames(rows)):
        px = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)
        if gains is not None:
            px *= gains[i]
        ff.stdin.write(np.clip(px, 0, 255).astype(np.uint8).tobytes())
    ff.stdin.close()
    if ff.wait() != 0:
        raise SystemExit("ffmpeg failed")
    print(f"{len(rows)} frames -> {out} ({len(rows) / args.fps:.1f} s at {args.fps} fps)")


def _frames(rows):
    """Yield proxy JPEG bytes for rows, opening each folder's proxy tar once."""
    tar, path, members = None, None, None
    try:
        for folder, n, _, proxy_path in rows:
            if proxy_path != path:
                if tar:
                    tar.close()
                path = proxy_path
                tar = tarfile.open(path)
                members = {m.name: m for m in tar.getmembers()}
            yield tar.extractfile(members[f"{folder}/TLS_{n:09d}.jpg"]).read()
    finally:
        if tar:
            tar.close()


if __name__ == "__main__":
    main()
