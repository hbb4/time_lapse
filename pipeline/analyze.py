#!/usr/bin/env python3
"""
Phase 1 analysis pass: one read of every frame in a set of camera folders, writing

  * a row per frame in pipeline.db (time corrected for the camera's clock, sun position, brightness, colour,
    frame-to-frame change, camera shift, sharpness), and
  * a proxy tar per folder: upright 380x672 JPEGs, small enough to preview and
    re-analyse without touching the full-resolution frames again.

Works on loose folders and on tars made by pack_frames.py. Resumable: finished
folders are skipped, and an interrupted folder is redone from its start.

Usage:
  python3 -m pipeline.analyze --sequence 4
  python3 -m pipeline.analyze --folders TLST05B01403_251014111254 --limit 500
  python3 -m pipeline.analyze --status
"""

import argparse
import csv
import io
import os
import sqlite3
import sys
import tarfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import numpy as np
from PIL import Image

from . import config
from .clock import Clock
from .frames import iter_frames, jpeg_bytes, locate

GRID = 16  # frame-to-frame change is measured on a GRID x GRID block-mean image

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
  folder TEXT PRIMARY KEY, sequence_id INTEGER, source_kind TEXT, frames INTEGER,
  errors INTEGER, t_start TEXT, t_end TEXT, proxy_path TEXT, seconds REAL, done_at TEXT
);
CREATE TABLE IF NOT EXISTS frames (
  folder TEXT NOT NULL, n INTEGER NOT NULL,
  t_local TEXT, t_utc REAL, sun_elev REAL, sun_az REAL,
  luma REAL, p05 REAL, p50 REAL, p95 REAL,
  sky_luma REAL, sky_r REAL, sky_g REAL, sky_b REAL, sky_sat REAL, ground_luma REAL,
  sharpness REAL, diff_mean REAL, diff_max REAL, diff_sky REAL,
  dx_prev REAL, dy_prev REAL, peak_prev REAL, dx_ref REAL, dy_ref REAL, peak_ref REAL,
  error TEXT, t_camera TEXT,
  PRIMARY KEY (folder, n)
);
CREATE INDEX IF NOT EXISTS frames_time ON frames (t_utc);
"""
FRAME_COLS = ["folder", "n", "t_local", "t_utc", "sun_elev", "sun_az", "luma", "p05", "p50", "p95",
              "sky_luma", "sky_r", "sky_g", "sky_b", "sky_sat", "ground_luma", "sharpness",
              "diff_mean", "diff_max", "diff_sky", "dx_prev", "dy_prev", "peak_prev",
              "dx_ref", "dy_ref", "peak_ref", "error", "t_camera"]


def open_db(path=None):
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    if "t_camera" not in [r[1] for r in db.execute("PRAGMA table_info(frames)")]:
        # older index: until now t_local held the camera's own (EXIF) time
        db.execute("ALTER TABLE frames ADD COLUMN t_camera TEXT")
        db.execute("UPDATE frames SET t_camera = t_local")
        db.commit()
    return db


def exif_time(im):
    exif = im.getexif()
    raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)
    if not raw:
        return None
    return datetime.strptime(raw.strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")


def _hann(h, w):
    return np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)


def analyse_frame(args):
    """Worker: decode one frame at proxy scale and measure it. Runs in a separate process."""
    folder, n, kind, data = args
    out = {"folder": folder, "n": n}
    try:
        im = Image.open(io.BytesIO(jpeg_bytes(kind, data)))
        t = exif_time(im)
        w, h = im.size
        im.draft("RGB", (w // config.PROXY_SCALE, h // config.PROXY_SCALE))
        im = im.convert("RGB")
        if config.ROTATE_CW and folder not in config.UPRIGHT_FOLDERS:
            im = im.transpose(Image.Transpose.ROTATE_270)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=config.PROXY_QUALITY)
        out["proxy"] = buf.getvalue()

        rgb = np.asarray(im, dtype=np.float32) / 255.0
        y = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        H, W = y.shape
        sky_rows = int(H * config.SKY_FRACTION)
        ground_rows = int(H * config.GROUND_FRACTION)
        sky, ground = rgb[:sky_rows], y[H - ground_rows:]

        if t is not None:
            out["t_camera"] = t.isoformat(sep=" ")  # the camera's clock; corrected in process_folder
        out["luma"] = float(y.mean())
        out["p05"], out["p50"], out["p95"] = (float(v) for v in np.percentile(y[::2, ::2], [5, 50, 95]))
        out["sky_luma"] = float(y[:sky_rows].mean())
        out["sky_r"], out["sky_g"], out["sky_b"] = (float(v) for v in sky.reshape(-1, 3).mean(0))
        mx, mn = sky.max(2), sky.min(2)
        out["sky_sat"] = float(np.where(mx > 0.02, (mx - mn) / np.maximum(mx, 1e-6), 0).mean())
        out["ground_luma"] = float(ground.mean())
        lap = (-4 * ground[1:-1, 1:-1] + ground[:-2, 1:-1] + ground[2:, 1:-1]
               + ground[1:-1, :-2] + ground[1:-1, 2:])
        out["sharpness"] = float(lap.var() * 1e4)

        gh, gw = H // GRID, W // GRID
        out["grid"] = y[:gh * GRID, :gw * GRID].reshape(GRID, gh, GRID, gw).mean((1, 3))
        g = ground - ground.mean()
        out["spec"] = np.fft.rfft2(g * _hann(*g.shape)).astype(np.complex64)
    except Exception as e:  # a bad frame is recorded, not fatal
        out["error"] = f"{type(e).__name__}: {e}"[:300]
    return out


def phase_shift(spec_a, spec_b, shape):
    """Shift (dx, dy) of frame b relative to a, in proxy pixels, and the correlation peak (0..1)."""
    cross = spec_b * np.conj(spec_a)
    cross /= np.abs(cross) + 1e-9
    r = np.fft.irfft2(cross, s=shape)
    iy, ix = np.unravel_index(int(np.argmax(r)), r.shape)
    peak = float(r[iy, ix])

    def sub(c, m, p):  # parabolic sub-pixel refinement
        d = c[m - 1] - 2 * c[m] + c[(m + 1) % len(c)]
        return p + (0.5 * (c[m - 1] - c[(m + 1) % len(c)]) / d if d != 0 else 0.0)

    dy = sub(r[:, ix], iy, iy)
    dx = sub(r[iy, :], ix, ix)
    if dy > shape[0] / 2:
        dy -= shape[0]
    if dx > shape[1] / 2:
        dx -= shape[1]
    return float(dx), float(dy), peak


def folders_for(args):
    """(folder, sequence_id) pairs to analyse; sequence ids come from the inventory CSV if present."""
    rows = []
    if config.INVENTORY_CSV.is_file():
        rows = [r for r in csv.DictReader(open(config.INVENTORY_CSV)) if r["folder"] not in config.EXCLUDE_FOLDERS]
    for f in args.folders or []:
        if f in config.EXCLUDE_FOLDERS:
            sys.exit(f"{f} is in EXCLUDE_FOLDERS (config.py): not the window view")
    if args.folders:
        seq = {r["folder"]: int(r["sequence_id"]) for r in rows}
        return [(f, args.sequence_id or seq.get(f)) for f in args.folders]
    picked = [(r["folder"], int(r["sequence_id"])) for r in rows if int(r["sequence_id"]) in args.sequence]
    if not picked:
        sys.exit(f"no folders for sequence(s) {args.sequence} in {config.INVENTORY_CSV} "
                 f"(missing or no match); pass --folders instead")
    return sorted(picked)


class Tracker:
    """Carries the previous frame (and a fixed daytime reference) across folders of one run."""

    def __init__(self):
        self.prev = None
        self.ref = None

    def update(self, row):
        grid, spec = row.pop("grid", None), row.pop("spec", None)
        if grid is None:
            return
        shape = (spec.shape[0], (spec.shape[1] - 1) * 2)
        if self.prev is not None:
            pgrid, pspec = self.prev
            d = np.abs(grid - pgrid)
            sky_rows = int(GRID * config.SKY_FRACTION)
            row["diff_mean"], row["diff_max"] = float(d.mean()), float(d.max())
            row["diff_sky"] = float(d[:sky_rows].mean())
            row["dx_prev"], row["dy_prev"], row["peak_prev"] = phase_shift(pspec, spec, shape)
        if self.ref is None and (row.get("sun_elev") or -90) > 10:
            self.ref = spec
        if self.ref is not None:
            row["dx_ref"], row["dy_ref"], row["peak_ref"] = phase_shift(self.ref, spec, shape)
        self.prev = (grid, spec)


def process_folder(db, pool, tracker, folder, seq_id, args):
    kind, src = locate(folder)
    config.PROXY_DIR.mkdir(parents=True, exist_ok=True)
    proxy_path = config.PROXY_DIR / f"{folder}.tar"
    partial = proxy_path.with_name(proxy_path.name + ".partial")
    db.execute("DELETE FROM folders WHERE folder = ?", (folder,))  # redone from scratch
    db.execute("DELETE FROM frames WHERE folder = ?", (folder,))
    t0 = time.time()
    count = errors = missing = 0
    t_first = t_last = None
    inflight, batch = [], []
    clock = Clock(db)
    if not clock.stretches:
        print("  clock: no offsets measured yet, trusting the camera's clock "
              "(run pipeline.clock scan, then apply)", flush=True)
    window = args.workers * 4

    def finish_one(tar):
        nonlocal count, errors, missing, t_first, t_last
        row = inflight.pop(0).result()
        proxy = row.pop("proxy", None)
        if row.get("t_camera"):  # camera time to real local time, using the offsets from pipeline.clock
            row["t_local"], row["t_utc"], row["sun_elev"], row["sun_az"] = clock.convert(
                datetime.fromisoformat(row["t_camera"]))
        tracker.update(row)
        if proxy is not None:
            info = tarfile.TarInfo(f"{folder}/TLS_{row['n']:09d}.jpg")
            info.size = len(proxy)
            info.mtime = int(row.get("t_utc") or 0)
            tar.addfile(info, io.BytesIO(proxy))
        if row.get("error"):
            errors += 1
            missing += row["error"].startswith("FileNotFoundError")
        if row.get("t_local"):
            t_first = t_first or row["t_local"]
            t_last = row["t_local"]
        batch.append([row.get(c) for c in FRAME_COLS])
        count += 1
        if len(batch) >= 2000:
            flush()
        if count % 1000 == 0:
            rate = count / (time.time() - t0)
            print(f"  {folder}: {count} frames ({rate:.1f}/s)", flush=True)

    def flush():
        db.executemany(f"INSERT OR REPLACE INTO frames ({','.join(FRAME_COLS)}) "
                       f"VALUES ({','.join('?' * len(FRAME_COLS))})", batch)
        batch.clear()

    print(f"{folder}: reading {kind} {src}", flush=True)
    with tarfile.open(partial, "w", format=tarfile.PAX_FORMAT) as tar:
        for i, (n, k, data) in enumerate(iter_frames(kind, src)):
            if args.limit and i >= args.limit:
                break
            inflight.append(pool.submit(analyse_frame, (folder, n, k, data)))
            while len(inflight) > window:
                finish_one(tar)
        while inflight:
            finish_one(tar)
    flush()
    os.replace(partial, proxy_path)
    secs = time.time() - t0
    if missing:
        # reclaim_space.sh deleted originals mid-read; the next run reads the new tar
        print(f"{folder}: {missing} frames vanished while reading (packed meanwhile?); "
              f"not marking done, rerun to redo it from the tar", flush=True)
    elif not args.limit:  # a --limit test run never marks a folder as done
        db.execute("INSERT OR REPLACE INTO folders VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))",
                   (folder, seq_id, kind, count, errors, t_first, t_last, str(proxy_path), secs))
    db.commit()
    print(f"{folder}: done, {count} frames, {errors} errors, {secs / 60:.1f} min "
          f"({count / max(secs, 1e-6):.1f} frames/s)", flush=True)


def status(db):
    rows = db.execute("SELECT folder, sequence_id, source_kind, frames, errors, t_start, t_end, "
                      "round(seconds/60, 1), done_at FROM folders ORDER BY folder").fetchall()
    for r in rows:
        print(" | ".join("" if v is None else str(v) for v in r))
    total = db.execute("SELECT count(*), sum(error IS NOT NULL) FROM frames").fetchone()
    print(f"{len(rows)} folders done, {total[0]} frame rows, {total[1] or 0} frame errors")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sequence", type=int, nargs="+", help="sequence id(s) from frame_folders.csv")
    ap.add_argument("--folders", nargs="+", help="explicit folder names, in order")
    ap.add_argument("--sequence-id", type=int, help="sequence id to record for --folders")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 2))
    ap.add_argument("--limit", type=int, help="only the first N frames of each folder (testing)")
    ap.add_argument("--redo", action="store_true", help="re-analyse folders already marked done")
    ap.add_argument("--status", action="store_true", help="print what has been analysed and exit")
    args = ap.parse_args()

    db = open_db()
    if args.status:
        status(db)
        return
    if not (args.sequence or args.folders):
        ap.error("give --sequence or --folders")

    done = {r[0] for r in db.execute("SELECT folder FROM folders")}
    todo = [(f, s) for f, s in folders_for(args) if args.redo or args.limit or f not in done]
    print(f"{len(todo)} folder(s) to analyse; output in {config.OUT_ROOT}", flush=True)
    tracker = Tracker()
    with ProcessPoolExecutor(args.workers) as pool:
        for folder, seq_id in todo:
            process_folder(db, pool, tracker, folder, seq_id, args)
    status(db)


if __name__ == "__main__":
    main()
