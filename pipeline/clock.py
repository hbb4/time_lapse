#!/usr/bin/env python3
"""
Work out how far the camera's clock is from real San Francisco time, from the images.

The camera stamps each frame with its own clock, which is not adjusted for daylight
saving and may have been reset. `scan` samples every folder about every 10 minutes
(EXIF time plus sky brightness), finds when the sky brightens each morning and darkens
each evening, and compares those times with civil dawn and dusk. The result is an
offset per camera day (camera time minus real local time), grouped into stretches
where the camera sat at one fixed UTC offset.

  scan   sample folders not scanned yet (resumable), then estimate and print offsets
  show   print the stretches and the days that don't fit
  apply  correct t_local, t_utc and the sun position of every frame in pipeline.db
         (the camera's own time is kept in t_camera, so this can be repeated safely)

Frames are only read. Clips must be re-planned after `apply`.

Usage:
  python3 -m pipeline.clock scan
  python3 -m pipeline.clock show
  python3 -m pipeline.clock apply
"""

import argparse
import io
import os
import re
import sys
import tarfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone

import numpy as np
from PIL import Image

from . import config
from .frames import FRAME_RE, jpeg_bytes, locate
from .sun import sun_events, sun_position

FOLDER_RE = re.compile(r"^TLS\w+_\d{12}$")
SAMPLE_SECONDS = 600     # one sample about every 10 minutes
CROSSING = 0.30          # sky is this far from its night level to its day level at civil dawn and dusk
MAX_GAP = 2700           # a crossing needs samples no more than 45 minutes apart

SCHEMA = """
CREATE TABLE IF NOT EXISTS clock_samples (
  folder TEXT NOT NULL, n INTEGER NOT NULL, t_camera TEXT, sky REAL, PRIMARY KEY (folder, n)
);
CREATE TABLE IF NOT EXISTS clock_folders (
  folder TEXT PRIMARY KEY, source TEXT, samples INTEGER, interval_s REAL, seconds REAL, done_at TEXT
);
CREATE TABLE IF NOT EXISTS clock_offsets (
  camera_day TEXT PRIMARY KEY, offset_hours INTEGER, raw_minutes REAL, dawn_minutes REAL,
  dusk_minutes REAL, confidence TEXT, utc_offset_hours INTEGER
);
CREATE TABLE IF NOT EXISTS clock_stretches (
  start_day TEXT PRIMARY KEY, end_day TEXT, utc_offset_hours INTEGER, days INTEGER, confident_days INTEGER
);
"""


def ensure_schema(db):
    """Create the clock tables and give `frames` a t_camera column holding the untouched EXIF time."""
    db.executescript(SCHEMA)
    cols = [r[1] for r in db.execute("PRAGMA table_info(frames)")]
    if cols and "t_camera" not in cols:
        db.execute("ALTER TABLE frames ADD COLUMN t_camera TEXT")
        db.execute("UPDATE frames SET t_camera = t_local")  # until now t_local was the EXIF time
    db.commit()


# --- camera time to real time ----------------------------------------------------------

class Clock:
    """Turns camera times into real local time using the stretches found by `scan`."""

    def __init__(self, db):
        db.executescript(SCHEMA)
        self.stretches = [(date.fromisoformat(a), date.fromisoformat(b), u) for a, b, u in
                          db.execute("SELECT start_day, end_day, utc_offset_hours FROM clock_stretches "
                                     "ORDER BY start_day")]
        self.warned = set()

    def utc_offset(self, day):
        """The camera's UTC offset in hours on a camera day, or None if nothing has been scanned."""
        if not self.stretches:
            return None
        for a, b, u in self.stretches:
            if a <= day <= b:
                return u
        a, b, u = min(self.stretches, key=lambda s: min(abs((day - s[0]).days), abs((day - s[1]).days)))
        if day not in self.warned:
            self.warned.add(day)
            print(f"  clock: no offset measured for {day}; using the nearest stretch ({a} to {b}, "
                  f"UTC{u:+d}). Run pipeline.clock scan to measure it.", flush=True)
        return u

    def convert(self, t_camera):
        """(t_local text, t_utc, sun elevation, sun azimuth) for a camera time (naive datetime)."""
        u = self.utc_offset(t_camera.date())
        if u is None:  # nothing scanned: take the camera's word for it
            aware = t_camera.replace(tzinfo=config.TZ)
        else:
            aware = t_camera.replace(tzinfo=timezone(timedelta(hours=u))).astimezone(config.TZ)
        elev, az = sun_position(aware)
        return aware.replace(tzinfo=None).isoformat(sep=" "), aware.timestamp(), elev, az


# --- scan --------------------------------------------------------------------------------

def measure(args):
    """Worker: EXIF time and sky brightness of one frame, from a small draft decode."""
    n, kind, data, upright = args
    try:
        im = Image.open(io.BytesIO(jpeg_bytes(kind, data)))
        exif = im.getexif()
        raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)
        t = datetime.strptime(raw.strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S").isoformat(sep=" ") if raw else None
        w, h = im.size
        im.draft("L", (w // 8, h // 8))
        im = im.convert("L")
        if config.ROTATE_CW and not upright:
            im = im.transpose(Image.Transpose.ROTATE_270)
        y = np.asarray(im, dtype=np.float32) / 255.0
        return n, t, float(y[:int(y.shape[0] * config.SKY_FRACTION)].mean())
    except Exception:
        return n, None, None


def library_folders():
    names = set()
    if config.FRAMES_ROOT.is_dir():
        names |= {e.name for e in os.scandir(config.FRAMES_ROOT) if e.is_dir() and FOLDER_RE.match(e.name)}
    for d in config.PACKED_DIRS:
        if d.is_dir():
            names |= {e.name[:-4] for e in os.scandir(d) if e.name.endswith(".tar") and FOLDER_RE.match(e.name[:-4])}
    return sorted(names - config.EXCLUDE_FOLDERS)


def folder_entries(kind, src):
    """(n, read) for every frame of a folder, in order; read() returns (kind, data) for measure()."""
    if kind == "loose":
        out = []
        with os.scandir(src) as it:
            for e in it:
                m = FRAME_RE.match(e.name)
                if m and m.group(2) == "jpg":
                    out.append((int(m.group(1)), lambda p=e.path: ("path", p)))
        return sorted(out, key=lambda x: x[0]), None
    tar = tarfile.open(src, "r:")  # seekable: listing reads only the headers, not the frames
    out = []
    for m in tar:
        parts = m.name.split("/")
        match = FRAME_RE.match(parts[-1]) if len(parts) == 2 and m.isfile() else None
        if match:
            out.append((int(match.group(1)), lambda m=m, k=match.group(2): (k, tar.extractfile(m).read())))
    return sorted(out, key=lambda x: x[0]), tar


def scan_folder(db, pool, folder):
    t0 = time.time()
    if db.execute("SELECT 1 FROM folders WHERE folder = ?", (folder,)).fetchone():
        # already analysed: the index has the EXIF time and sky brightness of every frame
        rows = db.execute("SELECT n, t_camera, sky_luma FROM frames WHERE folder = ? AND error IS NULL "
                          "ORDER BY n", (folder,)).fetchall()
        ts = [datetime.fromisoformat(r[1]) for r in rows[:200] if r[1]]
        interval = float(np.median(np.diff([t.timestamp() for t in ts]))) if len(ts) > 2 else 15.0
        samples = rows[::max(1, round(SAMPLE_SECONDS / max(interval, 1)))]
        source = "index"
    else:
        try:
            kind, src = locate(folder)
            entries, tar = folder_entries(kind, src)
        except (FileNotFoundError, tarfile.TarError, OSError) as e:
            print(f"{folder}: skipped ({e})", flush=True)
            return
        try:
            if len(entries) < 2:
                print(f"{folder}: skipped (no frames)", flush=True)
                return
            # the shot interval, from two frames a little apart, sets how sparsely to sample
            k = min(20, len(entries) - 1)
            upright = folder in config.UPRIGHT_FOLDERS
            a, b = (measure((n, *read(), upright)) for n, read in (entries[0], entries[k]))
            interval = 15.0
            if a[1] and b[1]:
                span = (datetime.fromisoformat(b[1]) - datetime.fromisoformat(a[1])).total_seconds()
                interval = max(span / max(entries[k][0] - entries[0][0], 1), 1.0)
            picked = entries[::max(1, round(SAMPLE_SECONDS / interval))]
            samples, inflight = [], []
            for n, read in picked:
                try:
                    inflight.append(pool.submit(measure, (n, *read(), upright)))
                except OSError:  # a loose frame deleted by reclaim_space.sh while scanning
                    samples.append((n, None, None))
                while len(inflight) > 32:
                    samples.append(inflight.pop(0).result())
            samples += [f.result() for f in inflight]
        finally:
            if tar is not None:
                tar.close()
        source = kind
    good = [s for s in samples if s[1] and s[2] is not None]
    db.execute("DELETE FROM clock_samples WHERE folder = ?", (folder,))
    db.executemany("INSERT INTO clock_samples VALUES (?,?,?,?)", [(folder, *s) for s in good])
    secs = time.time() - t0
    if len(good) >= 0.8 * len(samples):  # otherwise it was being packed or deleted; redo next time
        db.execute("INSERT OR REPLACE INTO clock_folders VALUES (?,?,?,?,?,datetime('now'))",
                   (folder, source, len(good), interval, secs))
    db.commit()
    print(f"{folder}: {len(good)}/{len(samples)} samples from {source}, shot every {interval:.0f} s, "
          f"{secs:.0f} s", flush=True)


def scan(db, args):
    done = {r[0] for r in db.execute("SELECT folder FROM clock_folders")}
    todo = [f for f in (args.folders or library_folders())
            if f not in config.EXCLUDE_FOLDERS and (args.redo or f not in done)]
    print(f"{len(todo)} folder(s) to scan", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as pool:
        for folder in todo:
            scan_folder(db, pool, folder)
    print(f"scan took {(time.time() - t0) / 60:.1f} min", flush=True)
    estimate(db)
    show(db)


# --- estimate ----------------------------------------------------------------------------

def _wall(dt):
    """Seconds on a clock face: a naive datetime as if it were UTC, so differences are plain."""
    return dt.replace(tzinfo=timezone.utc).timestamp()


_events = {}


def _true_event(name, near):
    """Real local wall time (as _wall seconds) of the civil dawn or dusk nearest to a wall time."""
    best = None
    d0 = datetime.fromtimestamp(near, timezone.utc).date()
    for d in (d0 - timedelta(days=1), d0, d0 + timedelta(days=1)):
        if d not in _events:
            _events[d] = sun_events(d, config.TZ)
        ev = _events[d].get(name)
        if ev is not None:
            w = _wall(ev.replace(tzinfo=None))
            if best is None or abs(w - near) < abs(best - near):
                best = w
    return best


def _crossings(t, y, thr, rising):
    """Times where brightness passes thr and stays on the new side for the next half hour."""
    out = []
    for k in range(len(t) - 1):
        if t[k + 1] - t[k] > MAX_GAP:
            continue
        a, b = (y[k], y[k + 1]) if rising else (y[k + 1], y[k])
        if not (a < thr <= b):
            continue
        before = y[max(k - 2, 0):k + 1][t[max(k - 2, 0):k + 1] >= t[k] - MAX_GAP]
        after = y[k + 1:k + 4][t[k + 1:k + 4] <= t[k + 1] + MAX_GAP]
        lo_side, hi_side = (before, after) if rising else (after, before)
        if np.median(lo_side) < thr <= np.median(hi_side):
            out.append(t[k] + (thr - y[k]) / (y[k + 1] - y[k]) * (t[k + 1] - t[k]))
    return out


def estimate(db):
    """Fill clock_offsets (one row per camera day) and clock_stretches from the samples."""
    rows = db.execute("SELECT t_camera, sky FROM clock_samples WHERE t_camera IS NOT NULL AND sky IS NOT NULL "
                      f"AND {config.not_excluded()} ORDER BY t_camera").fetchall()
    db.execute("DELETE FROM clock_offsets")
    db.execute("DELETE FROM clock_stretches")
    if not rows:
        db.commit()
        return
    t = np.array([_wall(datetime.fromisoformat(r[0])) for r in rows])
    y = np.array([r[1] for r in rows])
    days = sorted({datetime.fromisoformat(r[0]).date() for r in rows})
    per_day = []
    for d in days:
        s0 = _wall(datetime(d.year, d.month, d.day))
        near = (t >= s0 - 12 * 3600) & (t < s0 + 36 * 3600)
        if near.sum() < 30:
            continue
        lo, hi = np.percentile(y[near], [5, 95])
        if hi - lo < 0.2 or lo > 0.2:  # no clear night and day in reach, or not a view of the sky
            continue
        thr = lo + CROSSING * (hi - lo)
        tn, yn = t[near], y[near]
        diffs = {}
        for name, rising in (("dawn", True), ("dusk", False)):
            cands = [(c - _true_event(name, c)) / 60 for c in _crossings(tn, yn, thr, rising)
                     if s0 <= c < s0 + 86400]
            if cands:
                diffs[name] = min(cands, key=abs)
        if not diffs:
            continue
        raw = float(np.mean(list(diffs.values())))
        hours = int(round(raw / 60))
        near_hour = abs(raw - 60 * hours) <= 20
        if len(diffs) == 2:
            conf = "high" if near_hour and abs(diffs["dawn"] - diffs["dusk"]) <= 25 else "low"
        else:  # only a dawn or only a dusk (footage starts or stops that day)
            conf = "medium" if near_hour else "low"
        noon = datetime(d.year, d.month, d.day, 12, tzinfo=config.TZ)
        utc = int(round(noon.utcoffset().total_seconds() / 3600)) + hours
        per_day.append((d, hours, raw, diffs.get("dawn"), diffs.get("dusk"), conf, utc))
    db.executemany("INSERT INTO clock_offsets VALUES (?,?,?,?,?,?,?)",
                   [(d.isoformat(), h, round(r, 1), None if a is None else round(a, 1),
                     None if b is None else round(b, 1), c, u) for d, h, r, a, b, c, u in per_day])

    # Stretches of one camera UTC offset. A change needs two usable days in a row to
    # agree, so one odd day (fog at dawn, a gap) cannot split a stretch.
    sure = [p for p in per_day if p[5] != "low"] or per_day
    stretches = []
    for i, p in enumerate(sure):
        u = p[6]
        if not stretches:
            stretches.append([p[0], p[0], u])
        elif u == stretches[-1][2]:
            stretches[-1][1] = p[0]
        elif i + 1 < len(sure) and sure[i + 1][6] == u:
            stretches.append([p[0], p[0], u])
    for i, s in enumerate(stretches):  # stretch every one out to cover the days between
        if i == 0:
            s[0] = per_day[0][0]
        if i + 1 < len(stretches):
            s[1] = stretches[i + 1][0] - timedelta(days=1)
        else:
            s[1] = per_day[-1][0]
    for a, b, u in stretches:
        inside = [p for p in per_day if a <= p[0] <= b]
        db.execute("INSERT INTO clock_stretches VALUES (?,?,?,?,?)",
                   (a.isoformat(), b.isoformat(), u, len(inside), sum(p[5] != "low" for p in inside)))
    db.commit()


def show(db):
    stretches = db.execute("SELECT start_day, end_day, utc_offset_hours, days, confident_days "
                           "FROM clock_stretches ORDER BY start_day").fetchall()
    if not stretches:
        print("no clock offsets yet; run: python3 -m pipeline.clock scan")
        return
    print("Camera clock, by stretch (camera time minus real San Francisco time):")
    for a, b, u, n, sure in stretches:
        print(f"  {a} to {b}: camera at UTC{u:+d}  ({n} days measured, {sure} confident)")
        runs = []
        for day, h in db.execute("SELECT camera_day, offset_hours FROM clock_offsets WHERE camera_day BETWEEN ? "
                                 "AND ? AND utc_offset_hours = ? ORDER BY camera_day", (a, b, u)):
            if runs and runs[-1][2] == h:
                runs[-1][1] = day
            else:
                runs.append([day, day, h])
        for r0, r1, h in runs:
            print(f"      {r0} to {r1}: {h:+d} h against local time")
    odd = db.execute("SELECT o.camera_day, o.offset_hours, o.raw_minutes, o.dawn_minutes, o.dusk_minutes, "
                     "o.confidence, o.utc_offset_hours, s.utc_offset_hours FROM clock_offsets o "
                     "LEFT JOIN clock_stretches s ON o.camera_day BETWEEN s.start_day AND s.end_day "
                     "WHERE o.confidence = 'low' OR o.utc_offset_hours != s.utc_offset_hours "
                     "ORDER BY o.camera_day").fetchall()
    conflict = [r for r in odd if r[6] != r[7]]
    print(f"{len(odd)} low-confidence day(s), of which {len(conflict)} disagree with their stretch:")
    for day, h, raw, dawn, dusk, conf, u, su in conflict:
        print(f"  {day}: measured {raw:+.0f} min (dawn {dawn if dawn is not None else '-'}, "
              f"dusk {dusk if dusk is not None else '-'}), {conf} confidence; stretch says UTC{su:+d}")
    res = [r[0] for r in db.execute(
        "SELECT o.raw_minutes - 60 * o.offset_hours FROM clock_offsets o WHERE o.confidence = 'high'")]
    if res:
        print(f"confident days: {len(res)}, leftover after rounding to the hour: median {np.median(res):+.1f} min, "
              f"worst {max(res, key=abs):+.1f} min")


# --- apply -------------------------------------------------------------------------------

def apply(db):
    clock = Clock(db)
    if not clock.stretches:
        sys.exit("no clock offsets yet; run: python3 -m pipeline.clock scan")
    rows = db.execute("SELECT folder, n, t_camera, t_local FROM frames WHERE t_camera IS NOT NULL").fetchall()
    changed, updates = {}, []
    for folder, n, t_camera, t_local in rows:
        new_local, t_utc, elev, az = clock.convert(datetime.fromisoformat(t_camera))
        if new_local != t_local:
            changed[t_camera[:10]] = changed.get(t_camera[:10], 0) + 1
        updates.append((new_local, t_utc, elev, az, folder, n))
    db.executemany("UPDATE frames SET t_local = ?, t_utc = ?, sun_elev = ?, sun_az = ? WHERE folder = ? AND n = ?",
                   updates)
    db.commit()
    print(f"{len(rows)} frames checked, {sum(changed.values())} changed")
    for day in sorted(changed):
        print(f"  camera day {day}: {changed[day]} frames moved")
    if changed:
        print("now re-plan the clips: python3 -m pipeline.clips plan")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["scan", "show", "apply", "estimate"])
    ap.add_argument("--folders", nargs="+", help="scan only these folders")
    ap.add_argument("--redo", action="store_true", help="scan folders again even if already scanned")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    from .analyze import open_db
    db = open_db()
    ensure_schema(db)
    if args.command == "scan":
        scan(db, args)
    elif args.command == "estimate":
        estimate(db)
        show(db)
    elif args.command == "show":
        show(db)
    else:
        apply(db)


if __name__ == "__main__":
    main()
