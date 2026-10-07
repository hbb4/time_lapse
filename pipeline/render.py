"""
Render planned clips to video: quick samples from the proxies, or full resolution
from the original frames (loose or packed). Called by `pipeline.clips samples|render`.

Each frame is rotated upright, cross-faded with the next one where the plan asks for
an in-between frame, deflickered (gain onto a smoothed brightness curve),
cropped to 9:16 or 16:9 at the recipe's pan position, and stamped with its capture
time in the bottom-right corner; ffmpeg encodes the result.
"""

import io
import json
import multiprocessing
import os
import subprocess
import tarfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config
from .frames import iter_frames, jpeg_bytes, locate
from .preview import smooth_gain

FONT_PATHS = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
              "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def out_size(aspect, proxy):
    w, h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    return (w // 2, h // 2) if proxy else (w, h)


def crop_box(w, h, aspect, p):
    """Largest crop of the given aspect inside w x h, slid along the free axis by p (0..1)."""
    ax, ay = (9, 16) if aspect == "9:16" else (16, 9)
    if w * ay > h * ax:            # source is wider than the target: slide left-right
        cw, ch = round(h * ax / ay), h
        x = round(p * (w - cw))
        return x, 0, x + cw, ch
    cw, ch = w, round(w * ay / ax)  # source is taller: slide up-down
    y = round(p * (h - ch))
    return 0, y, cw, y + ch


def _font(size):
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def process_frame(args):
    """Worker: one source frame in, one finished RGB video frame (bytes) out."""
    kind, data, other, weight, upright, gain, pan, aspect, size, stamp = args
    im = Image.open(io.BytesIO(jpeg_bytes(kind, data))).convert("RGB")
    if other is not None:  # an in-between frame: cross-fade towards the next source frame
        im = Image.blend(im, Image.open(io.BytesIO(jpeg_bytes(*other))).convert("RGB"), weight)
    if not upright and config.ROTATE_CW:
        im = im.transpose(Image.Transpose.ROTATE_270)
    if gain is not None and abs(gain - 1) > 1e-3:
        lut = [min(255, round(v * gain)) for v in range(256)] * 3
        im = im.point(lut)
    im = im.crop(crop_box(*im.size, aspect, pan)).resize(size, Image.Resampling.LANCZOS)
    if stamp:
        d = ImageDraw.Draw(im)
        fs = max(12, round(0.04 * min(size)))
        font = _font(fs)
        x1, y1 = size[0] - round(fs * 0.8), size[1] - round(fs * 0.8)
        box = d.textbbox((0, 0), stamp, font=font)
        x, y = x1 - (box[2] - box[0]), y1 - (box[3] - box[1])
        d.text((x + 2, y + 2), stamp, font=font, fill=(0, 0, 0))
        d.text((x, y), stamp, font=font, fill=(255, 255, 255))
    return im.tobytes()


def source_frames(frames, proxy):
    """Yield (kind, data) for each planned frame, in order. frames = [[folder, n, t_local], ...]."""
    i = 0
    while i < len(frames):
        folder = frames[i][0]
        j = i
        while j < len(frames) and frames[j][0] == folder:
            j += 1
        wanted = [fr[1] for fr in frames[i:j]]
        if proxy:
            with tarfile.open(config.PROXY_DIR / f"{folder}.tar") as tar:
                members = {m.name: m for m in tar.getmembers()}
                for n in wanted:
                    yield "jpg", tar.extractfile(members[f"{folder}/TLS_{n:09d}.jpg"]).read()
        else:
            kind, src = locate(folder)
            if kind == "loose":
                for n in wanted:
                    yield "path", str(Path(src) / f"TLS_{n:09d}.jpg")
            else:
                want = set(wanted)
                got = {}
                for n, k, data in iter_frames(kind, src, wanted[0], wanted[-1]):
                    if n in want:
                        got[n] = (k, data)
                        # yield in order as soon as the next wanted frame is in hand
                        while wanted and wanted[0] in got:
                            yield got.pop(wanted.pop(0))
                if wanted:
                    raise FileNotFoundError(f"{folder}: frames {wanted[:5]}... missing from {src}")
        i = j


def planned_frames(frames, proxy):
    """Yield (first, second, weight) per planned frame; second is None unless it is a cross-fade.

    Each source frame is read once, however many planned frames use it.
    """
    keys, seen = [], set()
    for fr in frames:
        for key in [(fr[0], fr[1])] + ([(fr[3], fr[4])] if len(fr) > 3 else []):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    source = zip(keys, source_frames([list(k) for k in keys], proxy))
    held = {}
    for fr in frames:
        a, b = (fr[0], fr[1]), ((fr[3], fr[4]) if len(fr) > 3 else None)
        while a not in held or (b and b not in held):
            key, item = next(source)
            held[key] = item
        yield held[a], held[b] if b else None, fr[5] if b else 0.0
        while len(held) > 4:  # later frames never reach back this far
            del held[next(iter(held))]


def smoothstep(x):
    return x * x * (3 - 2 * x)


def render_one(db, pool, clip_id, proxy, workers):
    recipe, day, plan_json, settings_json = db.execute(
        "SELECT recipe, day, plan, settings FROM clips WHERE id = ?", (clip_id,)).fetchone()
    frames, s = json.loads(plan_json), json.loads(settings_json)
    size = out_size(s["aspect"], proxy)
    if proxy:
        out = config.OUT_ROOT / "samples" / recipe / f"{day}_{recipe}_{clip_id}.mp4"
    else:
        out = config.OUT_ROOT / "renders" / recipe / f"{day}_{recipe}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.stem + ".partial.mp4")

    gains = [None] * len(frames)
    if s.get("deflicker", True):
        folders = sorted({f[0] for f in frames} | {f[3] for f in frames if len(f) > 3})
        luma = {(f, n): l for f, n, l in db.execute(
            "SELECT folder, n, luma FROM frames WHERE folder IN (%s)" % ",".join("?" * len(folders)), folders)}
        gains = list(smooth_gain([luma[(f[0], f[1])] if len(f) == 3 else
                                  (1 - f[5]) * luma[(f[0], f[1])] + f[5] * luma[(f[3], f[4])] for f in frames], 9))

    enc = (["-c:v", "libx264", "-crf", "23", "-preset", "veryfast"] if proxy
           else ["-c:v", "libx264", "-crf", "18", "-preset", "medium"])
    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{size[0]}x{size[1]}", "-framerate", str(s["fps"]), "-i", "-", *enc,
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(partial)],
        stdin=subprocess.PIPE)
    t0 = time.time()
    p0, p1 = s.get("pan", [0.5, 0.5])
    last = max(len(frames) - 1, 1)
    inflight = []
    try:
        for i, ((kind, data), other, weight) in enumerate(planned_frames(frames, proxy)):
            pan = p0 + (p1 - p0) * smoothstep(i / last)
            stamp = frames[i][2][:16] if s.get("timestamp", True) else None
            inflight.append(pool.submit(process_frame, (kind, data, other, weight, proxy, gains[i], pan, s["aspect"], size, stamp)))
            while len(inflight) > workers * 3:
                ff.stdin.write(inflight.pop(0).result())
        while inflight:
            ff.stdin.write(inflight.pop(0).result())
        ff.stdin.close()
        if ff.wait() != 0:
            raise RuntimeError("ffmpeg failed")
    except BaseException:
        ff.kill()
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, out)
    col = "sample_path" if proxy else "render_path"
    db.execute(f"UPDATE clips SET {col} = ? WHERE id = ?", (str(out), clip_id))
    db.commit()
    print(f"  {recipe} {day} (#{clip_id}): {len(frames)} frames -> {out} in {time.time() - t0:.0f}s", flush=True)


def render_clips(db, rows, proxy, redo=False, workers=4):
    todo = []
    for cid, name, day, score, rank, frames, secs, status, sample, render in rows:
        existing = sample if proxy else render
        if existing and Path(existing).exists() and not redo:
            continue
        todo.append(cid)
    what = "samples" if proxy else "full-resolution renders"
    print(f"{len(todo)} {what} to make ({len(rows) - len(todo)} already done)", flush=True)
    # spawn, not fork: forked workers would inherit ffmpeg's stdin pipe and keep it open
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        for cid in todo:
            render_one(db, pool, cid, proxy, workers)
