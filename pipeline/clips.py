#!/usr/bin/env python3
"""
Turn recipes.toml into ranked clip candidates, using the per-frame metrics in pipeline.db.

For every recipe and every day of analysed footage this works out the time window,
scores the day (sky colour, moving structured cloud, fog burning off, local motion,
night activity), ranks it
0-100 against the other days for that recipe, and picks which frames the clip uses,
speeding up quiet stretches when the recipe allows it. Results go in the `clips`
table; your keep/reject choices survive re-planning.

Usage:
  python3 -m pipeline.clips plan                 # (re)plan every recipe
  python3 -m pipeline.clips list --recipe sunset
  python3 -m pipeline.clips samples --top 5      # quick proxy previews to review
  python3 -m pipeline.clips render --status keep # full-resolution renders
"""

import argparse
import io
import json
import re
import sys
import tarfile
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

from . import config
from .analyze import open_db
from .sun import sun_events

RECIPES_PATH = Path(__file__).resolve().parent.parent / "recipes.toml"
ANCHOR_RE = re.compile(r"^\s*(dawn|sunrise|noon|sunset|dusk|\d{1,2}:\d{2})\s*(?:([+-])\s*(\d+))?\s*$")
MIN_COVERAGE = 0.85  # skip windows where frames are missing for more than 15% of the time
STRUCTURE_EVERY = 8  # sky structure is measured on every 8th daytime frame (about 2 minutes apart)
# Structure of an empty blue sky (the gradient towards the horizon). Flat fog reads lower,
# broken cloud two to three times higher.
CLEAR_SKY_STRUCTURE = 0.03

CLIPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY, recipe TEXT NOT NULL, day TEXT NOT NULL,
  start_local TEXT, end_local TEXT, raw_score REAL, score REAL, rank INTEGER,
  frames INTEGER, seconds REAL, plan TEXT, settings TEXT,
  status TEXT NOT NULL DEFAULT 'new', note TEXT, sample_path TEXT, render_path TEXT,
  planned_at TEXT, UNIQUE (recipe, day)
);
CREATE TABLE IF NOT EXISTS sky (
  folder TEXT NOT NULL, n INTEGER NOT NULL, structure REAL, PRIMARY KEY (folder, n)
);
"""


@dataclass
class Recipe:
    name: str
    start: str
    end: str
    score: str = "clouds"
    fps: int = 30
    seconds_per_frame: float = 0  # 0 = every frame the camera shot
    blend: bool = False
    max_speedup: float = 1.0
    aspect: str = "9:16"
    pan: list = field(default_factory=lambda: [0.5, 0.5])
    timestamp: bool = True
    deflicker: bool = True
    min_score: float = 0
    top: int = 0

    def settings(self):
        return {k: getattr(self, k) for k in ("fps", "seconds_per_frame", "max_speedup", "aspect",
                                              "pan", "timestamp", "deflicker")}


def load_recipes(path=RECIPES_PATH):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    defaults = data.get("defaults", {})
    recipes = []
    for r in data.get("recipe", []):
        merged = {**defaults, **r}
        unknown = set(merged) - set(Recipe.__dataclass_fields__)
        if unknown:
            sys.exit(f"recipe {r.get('name')}: unknown setting(s) {', '.join(sorted(unknown))}")
        rec = Recipe(**merged)
        for s in (rec.start, rec.end):
            if not ANCHOR_RE.match(s):
                sys.exit(f"recipe {rec.name}: can't read time {s!r} (e.g. 'sunset-90', 'noon', '06:30+15')")
        if rec.score not in SCORERS:
            sys.exit(f"recipe {rec.name}: score must be one of {', '.join(SCORERS)}")
        if rec.aspect not in ("9:16", "16:9"):
            sys.exit(f"recipe {rec.name}: aspect must be 9:16 or 16:9")
        recipes.append(rec)
    return recipes


def resolve(spec, day):
    """Aware local datetime for an anchor spec like 'sunset-90' on a calendar day."""
    m = ANCHOR_RE.match(spec)
    anchor, sign, minutes = m.group(1), m.group(2), int(m.group(3) or 0)
    if ":" in anchor:
        hh, mm = map(int, anchor.split(":"))
        t = datetime(day.year, day.month, day.day, hh, mm, tzinfo=config.TZ)
    else:
        t = sun_events(day, config.TZ)[anchor]
    return t + timedelta(minutes=-minutes if sign == "-" else minutes)


def window(recipe, day):
    start = resolve(recipe.start, day)
    end = resolve(recipe.end, day)
    if end <= start:  # e.g. dusk to dawn: the end is on the next day
        end = resolve(recipe.end, day + timedelta(days=1))
    return start, end


FRAME_FIELDS = ["folder", "n", "t_utc", "t_local", "sun_elev", "luma", "sky_luma", "sky_r", "sky_g",
                "sky_b", "sky_sat", "diff_mean", "diff_max", "diff_sky"]


def load_frames(db, t0, t1):
    rows = db.execute(f"SELECT {','.join(FRAME_FIELDS)} FROM frames "
                      f"WHERE t_utc BETWEEN ? AND ? AND error IS NULL AND {config.not_excluded()} "
                      "ORDER BY t_utc", (t0, t1)).fetchall()
    if not rows:
        return None
    cols = list(zip(*rows))
    out = {k: np.array(v, dtype=float) for k, v in zip(FRAME_FIELDS, cols) if k not in ("folder", "t_local")}
    out["folder"] = list(cols[0])
    out["t_local"] = list(cols[3])
    for k in ("diff_mean", "diff_max", "diff_sky"):  # first frame of a run has no previous
        out[k] = np.nan_to_num(out[k], nan=np.nanmedian(out[k]) if np.isfinite(out[k]).any() else 0)
    sky = db.execute("SELECT f.t_utc, s.structure FROM sky s JOIN frames f USING (folder, n) "
                     "WHERE f.t_utc BETWEEN ? AND ? ORDER BY f.t_utc", (t0, t1)).fetchall()
    if sky:  # measured on a subset of frames; fill in between
        out["structure"] = np.interp(out["t_utc"], [r[0] for r in sky], [r[1] for r in sky])
    else:
        out["structure"] = np.zeros(len(rows))
    return out


def sky_structure(im):
    """How much cloud-sized light and dark detail the sky has, relative to its brightness.

    Flat fog and empty blue sky are both low; broken or textured cloud is high.
    """
    y = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
    y = y[:int(y.shape[0] * config.SKY_FRACTION)]
    h, w = y.shape
    b = y[:h // 8 * 8, :w // 8 * 8].reshape(h // 8, 8, w // 8, 8).mean((1, 3))
    b = b[1:, 2:-2]  # leave out the dark lens corners at the top
    edges = np.abs(np.diff(b, axis=1)).mean() + np.abs(np.diff(b, axis=0)).mean()
    return float(edges / max(float(b.mean()), 0.05))


def ensure_structure(db):
    """Measure sky structure from the proxies for daytime frames that don't have it yet."""
    todo = db.execute(
        "SELECT folder, n FROM frames f WHERE error IS NULL AND sun_elev > -6 AND n % ? = 0 "
        "AND NOT EXISTS (SELECT 1 FROM sky s WHERE s.folder = f.folder AND s.n = f.n) "
        "ORDER BY folder, n", (STRUCTURE_EVERY,)).fetchall()
    if not todo:
        return
    print(f"measuring sky structure on {len(todo)} proxy frames...", flush=True)
    by_folder = {}
    for folder, n in todo:
        by_folder.setdefault(folder, []).append(n)
    for folder, ns in by_folder.items():
        with tarfile.open(config.PROXY_DIR / f"{folder}.tar") as tar:
            members = {m.name: m for m in tar.getmembers()}
            vals = []
            for n in ns:
                m = members.get(f"{folder}/TLS_{n:09d}.jpg")
                if m is not None:
                    vals.append((folder, n, sky_structure(Image.open(io.BytesIO(tar.extractfile(m).read())))))
        db.executemany("INSERT OR REPLACE INTO sky (folder, n, structure) VALUES (?,?,?)", vals)
        db.commit()


def coverage(f, t0, t1):
    """Fraction of the window that has frames, counting gaps over 3x the usual interval as missing."""
    t = f["t_utc"]
    if len(t) < 2:
        return 0.0
    gaps = np.diff(t)
    usual = np.median(gaps)
    missing = gaps[gaps > 3 * usual].sum() + max(t[0] - t0, 0) + max(t1 - t[-1], 0)
    return max(0.0, 1 - missing / (t1 - t0))


# --- scores: higher is better; ranked into 0-100 within each recipe -----------------

def sky_activity(f):
    """Frame-to-frame sky change relative to sky brightness (so dim scenes are not penalised)."""
    return f["diff_sky"] / np.maximum(f["sky_luma"], 0.05)


def score_colour(f):
    vivid = f["sky_sat"] * np.sqrt(np.clip(f["sky_luma"], 0, 1))
    total = f["sky_r"] + f["sky_g"] + f["sky_b"]
    warmth = np.clip((f["sky_r"] - f["sky_b"]) / np.maximum(total, 1e-3), 0, None)
    return float(np.percentile(vivid, 90) + np.percentile(warmth, 90))


def score_clouds(f):
    """Structured cloud that is moving: flat fog churning and clear sky both score low."""
    day = f["sun_elev"] > -3
    if day.sum() <= 10:
        day = np.ones(len(day), dtype=bool)
    structure = np.clip(f["structure"][day] - CLEAR_SKY_STRUCTURE, 0, None)
    return float(np.mean(structure) * np.median(sky_activity(f)[day]))


def score_burnoff(f):
    """Grey sky turning blue: the biggest rise in sky colour from an earlier low to a later high.

    Frames with the sun under 8 degrees are left out, since a clear dawn also gains colour.
    """
    sat = f["sky_sat"][f["sun_elev"] > 8]
    if len(sat) < 60:
        return 0.0
    sat = rolling(sat, 61, np.median)[::10]  # about 15 minutes of footage shot every 15 s
    return float(np.max(sat - np.minimum.accumulate(sat)))


def score_motion(f):
    local = (f["diff_max"] - f["diff_mean"]) / np.maximum(f["luma"], 0.05)
    return float(np.median(local))


def score_night(f):
    a = float(np.median(sky_activity(f)))
    glow = f["sky_luma"]
    fog = float(np.std(glow) / max(np.mean(glow), 1e-3))  # fog lights the sky up and down
    return a + 0.5 * fog


SCORERS = {"colour": score_colour, "clouds": score_clouds, "burnoff": score_burnoff, "motion": score_motion,
           "night": score_night}


# --- variable speed ------------------------------------------------------------------

def rolling(x, n, fn):
    if len(x) == 0:
        return x
    half = n // 2
    p = np.pad(x, half, mode="edge")
    return np.array([fn(p[i:i + n]) for i in range(len(x))])


def gaussian_smooth(x, sigma):
    if sigma <= 0 or len(x) < 3:
        return x
    r = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(x, r, mode="edge"), k, mode="valid")


def speed_curve(f, recipe, quiet, busy):
    """Speed multiplier per source frame: 1 when busy, up to max_speedup when quiet."""
    n = len(f["t_utc"])
    if recipe.max_speedup <= 1 or busy <= quiet:
        return np.ones(n)
    a = rolling(sky_activity(f), 9, np.median)      # drop single-frame flicker
    a = gaussian_smooth(a, 6)
    calm = np.clip((busy - a) / (busy - quiet), 0, 1)
    return 1 + (recipe.max_speedup - 1) * calm


def pick_frames(f, recipe, speed):
    """Walk through the window in real time, advancing seconds_per_frame x speed per video frame.

    Returns (i, j, w) per video frame: source frame i (then j == i and w == 0), or, only
    when the recipe sets blend, a cross-fade from i to j by weight w where the camera shot
    less often than seconds_per_frame needs.
    """
    t = f["t_utc"]
    out = []
    if not recipe.seconds_per_frame:  # every frame in order, skipping whole frames when sped up
        pos = 0.0
        while round(pos) < len(t):
            i = int(round(pos))
            out.append((i, i, 0.0))
            pos += max(speed[i], 1.0)
        return out
    usual = float(np.median(np.diff(t))) if len(t) > 1 else recipe.seconds_per_frame
    cur = t[0]
    while cur <= t[-1]:
        j = min(max(int(np.searchsorted(t, cur, side="right")) - 1, 0), len(t) - 1)
        step = recipe.seconds_per_frame * speed[j]
        gap = t[j + 1] - t[j] if j + 1 < len(t) else 0
        w = (cur - t[j]) / gap if gap > 0 else 0
        if not recipe.blend or step >= 0.9 * usual or gap == 0 or gap > 3 * usual:  # use the nearest real frame
            i = j + 1 if w > 0.5 else j
            pick = (i, i, 0.0)
        else:
            w = round(w * 8) / 8
            pick = (j, j, 0.0) if w == 0 else (j + 1, j + 1, 0.0) if w == 1 else (j, j + 1, w)
        if not out or pick != out[-1]:
            out.append(pick)
        cur += step
    return out


def ramp_speed(speed, t, fps, seconds_per_frame):
    """Smooth the speed curve over about 2 s of video so speed changes never look like jumps."""
    if np.all(speed == 1):
        return speed
    interval = float(np.median(np.diff(t))) if len(t) > 1 else seconds_per_frame
    frames_per_2s = 2 * fps * (seconds_per_frame or interval) / max(interval, 1e-6)
    return gaussian_smooth(speed, max(frames_per_2s / 2, 1))


# --- planning ------------------------------------------------------------------------

def footage_days(db):
    lo, hi = db.execute("SELECT min(t_local), max(t_local) FROM frames WHERE error IS NULL "
                        f"AND {config.not_excluded()}").fetchone()
    if not lo:
        return []
    d0, d1 = date.fromisoformat(lo[:10]), date.fromisoformat(hi[:10])
    return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]


def plan(db, recipes, only=None):
    db.executescript(CLIPS_SCHEMA)
    days = footage_days(db)
    if not days:
        sys.exit("no analysed frames yet; run pipeline.analyze first")
    ensure_structure(db)
    for rec in recipes:
        if only and rec.name not in only:
            continue
        cands, skipped = [], 0
        for day in days:
            t0, t1 = window(rec, day)
            f = load_frames(db, t0.timestamp(), t1.timestamp())
            if f is None or coverage(f, t0.timestamp(), t1.timestamp()) < MIN_COVERAGE:
                skipped += 1
                continue
            cands.append((day, t0, t1, f, SCORERS[rec.score](f)))
        if not cands:
            print(f"{rec.name}: no day has full footage for {rec.start} to {rec.end}")
            continue

        # quiet/busy thresholds come from this recipe's footage as a whole, so a busy
        # day plays mostly at normal speed and a dead one mostly fast
        acts = np.concatenate([gaussian_smooth(rolling(sky_activity(c[3]), 9, np.median), 6) for c in cands])
        quiet, busy = np.percentile(acts, 30), np.percentile(acts, 70)
        raws = np.array([c[4] for c in cands])
        ranks = raws.argsort()[::-1].argsort()  # 0 = best
        pct = 100.0 * (raws.argsort().argsort() + 0.5) / len(raws) if len(raws) > 1 else np.array([100.0])

        for (day, t0, t1, f, raw), r, p in zip(cands, ranks, pct):
            speed = ramp_speed(speed_curve(f, rec, quiet, busy), f["t_utc"], rec.fps, rec.seconds_per_frame)
            frames = []  # [folder, n, time] or, cross-faded, [folder, n, time, folder2, n2, weight]
            for i, j, w in pick_frames(f, rec, speed):
                if w == 0:
                    frames.append([f["folder"][i], int(f["n"][i]), f["t_local"][i]])
                else:
                    frames.append([f["folder"][i], int(f["n"][i]), f["t_local"][j if w > 0.5 else i],
                                   f["folder"][j], int(f["n"][j]), w])
            db.execute(
                "INSERT INTO clips (recipe, day, start_local, end_local, raw_score, score, rank, frames, "
                "seconds, plan, settings, planned_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now')) "
                "ON CONFLICT (recipe, day) DO UPDATE SET start_local=excluded.start_local, "
                "end_local=excluded.end_local, raw_score=excluded.raw_score, score=excluded.score, "
                "rank=excluded.rank, frames=excluded.frames, seconds=excluded.seconds, plan=excluded.plan, "
                "settings=excluded.settings, planned_at=excluded.planned_at, "
                "sample_path=CASE WHEN clips.plan = excluded.plan THEN clips.sample_path END, "
                "render_path=CASE WHEN clips.plan = excluded.plan THEN clips.render_path END",
                (rec.name, day.isoformat(), t0.strftime("%Y-%m-%d %H:%M"), t1.strftime("%Y-%m-%d %H:%M"),
                 raw, round(float(p), 1), int(r) + 1, len(frames), round(len(frames) / rec.fps, 1),
                 json.dumps(frames), json.dumps(rec.settings())))
        # days that no longer fit the window: drop them unless you already marked them
        kept = [c[0].isoformat() for c in cands]
        db.execute("DELETE FROM clips WHERE recipe = ? AND status = 'new' AND day NOT IN (%s)"
                   % ",".join("?" * len(kept)), [rec.name, *kept])
        db.commit()
        print(f"{rec.name}: {len(cands)} days planned, {skipped} skipped (footage missing in window)")


def selected(db, recipes, args):
    """Clips matching the command line and each recipe's min_score / top."""
    by_name = {r.name: r for r in recipes}
    q = "SELECT id, recipe, day, score, rank, frames, seconds, status, sample_path, render_path FROM clips"
    rows = db.execute(q + " ORDER BY recipe, rank").fetchall()
    out = []
    for row in rows:
        cid, name, day, score, rank, *_ = row
        rec = by_name.get(name)
        if rec is None:
            continue  # recipe removed from recipes.toml
        if args.recipe and name not in args.recipe:
            continue
        if getattr(args, "id", None) and cid not in args.id:
            continue
        if getattr(args, "status", None) and row[7] not in args.status:
            continue
        if not getattr(args, "id", None):
            if score < rec.min_score or (rec.top and rank > rec.top):
                continue
            if getattr(args, "top", None) and rank > args.top:
                continue
        out.append(row)
    return out


def cmd_list(db, recipes, args):
    rows = selected(db, recipes, args)
    print(f"{'id':>4}  {'recipe':<16}{'day':<12}{'score':>6}{'rank':>6}{'frames':>7}{'secs':>6}  status   sample")
    for cid, name, day, score, rank, frames, secs, status, sample, render in rows:
        print(f"{cid:>4}  {name:<16}{day:<12}{score:>6.0f}{rank:>6}{frames:>7}{secs:>6.0f}  {status:<8} "
              f"{'yes' if sample else ''}")
    print(f"{len(rows)} clip(s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "list", "samples", "render"])
    ap.add_argument("--recipes", type=Path, default=RECIPES_PATH)
    ap.add_argument("--recipe", nargs="+", help="only these recipes")
    ap.add_argument("--id", type=int, nargs="+", help="only these clip ids (ignores min_score/top)")
    ap.add_argument("--status", nargs="+", choices=["new", "keep", "reject"])
    ap.add_argument("--top", type=int, help="only the N best days per recipe")
    ap.add_argument("--redo", action="store_true", help="render again even if a file exists")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    recipes = load_recipes(args.recipes)
    if args.command == "samples" and args.top is None and not args.id:
        args.top = 5
    db = open_db()
    db.executescript(CLIPS_SCHEMA)
    if args.command == "plan":
        plan(db, recipes, args.recipe)
        cmd_list(db, recipes, argparse.Namespace(recipe=args.recipe, top=5))
    elif args.command == "list":
        cmd_list(db, recipes, args)
    else:
        from .render import render_clips
        render_clips(db, selected(db, recipes, args), proxy=args.command == "samples",
                     redo=args.redo, workers=args.workers)


if __name__ == "__main__":
    main()
