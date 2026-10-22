"""Render every row of a clip sheet (CSV): passage + recording + footage span + look -> finished clips.

  .venv/bin/python captions/batch.py captions/clips.csv                 render all rows (skips finished clips)
  .venv/bin/python captions/batch.py captions/clips.csv --only fog_01   one row
  .venv/bin/python captions/batch.py captions/clips.csv --dry-run       show what each row resolves to

Columns (blank = default). Any of these can also be set inside the passage file with an "@column value"
line at the top (see GUIDE.md); a line in the passage file wins over the sheet.
  name          output name
  passage       .txt with the passage text and markup (see GUIDE.md for the key)
  recording     your reading (m4a, mp3, wav); the row is skipped until this file exists
  from, to      the footage: date-times like 2026-09-21 17:45 (spans can cross days and folders),
                or clock times (17:45, 20:35) together with a folder; a "to" earlier than "from" = next day
  folder        optional: camera folder name(s) (TLST05B01403_260921165611, several joined with +);
                needed only with clock times or frame numbers (#380, #1650)
  move          push | pull | tilt | still | drift
  style         pop | serif | type | whisper | poster (blank = random per clip)
  aspect        9x16 | 16x9 | both   (16x9 is the long-form look)
  fit           span (default: the whole span plays across the reading) | realtime (every frame once, 30 fps)
  ending        rewind | hold
  music, music_start, music_db, end_boost_db
  trails, exposure, beat_punch, beat_rewind (yes/no), fx_every (seconds between layout effects, 0 = only marked)
  end_card      image, GIF or video shown after the ending (blank = none); {aspect} in the name picks
                card_9x16.png or card_16x9.png per shape
  end_card_seconds, end_card_sound (music | own | a sound file)
  cover         auto (most dramatic frame, default) | a time in the clip like 12.5 | none
  safe          on (default) | off: vertical captions stay clear of the app buttons
  contrast      on (default) | off: stronger shadow over bright backgrounds
  blend         yes: blend neighbouring frames (default no: every video frame is one whole camera frame)
  intro         gust | fog | rush | dark | none (default): opening sound + matching screen effect (intro.py)
  intro_sound, intro_db, intro_seconds, lead (seconds before the reading starts; default per intro)
  rewind_sfx    a sound file, or whoosh / wind (synthesized) at the rewind
  voice_db, voice_clean (on | off), loudness (LUFS, default -14; 0 = off)

Every video frame is one whole camera frame. If the span has fewer frames than the reading needs, it is
widened evenly on both sides (from pipeline.db) so no frame repeats; the printout says when.
Renders run one at a time, at low priority with 2 threads (--threads 0 = full speed).

Footage comes from the pipeline: times are the clock-corrected ones in pipeline.db, the indoor folders are
skipped, and packed folders (_packed/*.tar) are read directly. The frames a clip needs are unpacked into
_captions/work/frames/<name>/ for the render and deleted afterwards (--keep-frames keeps them).
"""
import argparse, csv, json, os, shutil, sqlite3, subprocess, sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # the time_lapse repo root, for the pipeline package
from pipeline import config, frames as pframes   # noqa: E402
import intro, markup                                     # noqa: E402

OUT = str(config.FRAMES_ROOT / "_captions")
EXCLUDE = set(getattr(config, "EXCLUDE_FOLDERS", []))

def yes(v): return str(v).strip().lower() in ("yes", "y", "true", "1")

def when(v):
    return datetime.fromisoformat(v.strip().replace(" ", "T"))

def span_rows(db, r):
    """[(folder, frame number, t_local)] for the row's footage, in time order."""
    start, end = (r.get("from") or "").strip(), (r.get("to") or "").strip()
    folders = [f.strip().rstrip("/").split("/")[-1] for f in (r.get("folder") or "").split("+") if f.strip()]
    q = "SELECT folder, n, t_local FROM frames WHERE error IS NULL"
    skip = f" AND folder NOT IN ({','.join('?' * len(EXCLUDE))})" if EXCLUDE else ""
    if start.startswith("#") or end.startswith("#"):          # frame numbers within the named folders
        if not folders: raise SystemExit(f"{r['name']}: frame numbers need a folder")
        rows = []
        for k, f in enumerate(folders):
            lo = int(start[1:]) if k == 0 and start.startswith("#") else 0
            hi = int(end[1:]) if k == len(folders) - 1 and end.startswith("#") else 10 ** 9
            rows += db.execute(q + " AND folder = ? AND n BETWEEN ? AND ? ORDER BY n", (f, lo, hi)).fetchall()
        return rows
    if "-" not in start[:10]:                                 # clock times: the day comes from the folder
        if not folders: raise SystemExit(f"{r['name']}: clock times need a folder (or use full date-times)")
        day0 = db.execute("SELECT min(t_local) FROM frames WHERE folder = ?", (folders[0],)).fetchone()[0]
        if not day0: raise SystemExit(f"{folders[0]} is not in pipeline.db yet (run pipeline.analyze)")
        base = when(day0)
        def at(v):
            h, m = map(int, v.split(":")); t = base.replace(hour=h, minute=m, second=0)
            return t + timedelta(days=1) if t < base else t
        t0, t1 = at(start), at(end)
    else:
        t0, t1 = when(start), when(end)
    if t1 <= t0: t1 += timedelta(days=1)
    args = [t0.isoformat(sep=" "), t1.isoformat(sep=" ")] + sorted(EXCLUDE)
    return db.execute(q + " AND t_local >= ? AND t_local <= ?" + skip + " ORDER BY t_local", args).fetchall()

def widen(db, rows, need):
    """Grow a span evenly on both sides until it has `need` frames (whole frames, never repeated)."""
    if len(rows) >= need: return rows
    q = "SELECT folder, n, t_local FROM frames WHERE error IS NULL" + \
        (f" AND folder NOT IN ({','.join('?' * len(EXCLUDE))})" if EXCLUDE else "")
    ex = sorted(EXCLUDE)
    extra = need - len(rows)
    before = db.execute(q + " AND t_local < ? ORDER BY t_local DESC LIMIT ?", ex + [rows[0][2], extra // 2]).fetchall()[::-1]
    after = db.execute(q + " AND t_local > ? ORDER BY t_local LIMIT ?", ex + [rows[-1][2], extra - len(before)]).fetchall()
    if len(before) + len(after) < extra:          # ran into the start or end of the footage: take more from the other side
        before = db.execute(q + " AND t_local < ? ORDER BY t_local DESC LIMIT ?",
                            ex + [rows[0][2], extra - len(after)]).fetchall()[::-1]
    return before + rows + after

def materialize(rows, cache):
    """Frame paths for the render: loose JPEGs as they are, packed ones unpacked into the cache."""
    paths, by_folder = [], {}
    for f, n, _ in rows: by_folder.setdefault(f, []).append(n)
    got = {}
    for f, ns in by_folder.items():
        kind, path = pframes.locate(f)
        want = set(ns)
        for n, k, data in pframes.iter_frames(kind, path, min(ns), max(ns)):
            if n not in want: continue
            if k == "path":
                got[(f, n)] = data
            else:
                out = os.path.join(cache, f, f"TLS_{n:09d}.jpg")
                if not os.path.exists(out):
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with open(out, "wb") as fh: fh.write(pframes.jpeg_bytes(k, data))
                got[(f, n)] = out
    for f, n, _ in rows:
        if (f, n) in got: paths.append(got[(f, n)])
    return paths

def run_row(db, r, out_dir, dry, keep_frames, threads=2):
    r = dict(r)
    text = open(r["passage"].strip(), encoding="utf-8").read() if (r.get("passage") or "").strip() else ""
    r.update(markup.directives(text))                          # "@column value" lines in the passage win
    name = r["name"].strip()
    work = os.path.join(out_dir, "work"); os.makedirs(work, exist_ok=True)
    rows = span_rows(db, r)
    if not rows: raise SystemExit(f"{name}: no analysed frames in that span")
    print(f"{name}: {len(rows)} frames, {rows[0][2][:16]} to {rows[-1][2][:16]}"
          + (f" ({len({f for f, _, _ in rows})} folders)" if len({f for f, _, _ in rows}) > 1 else ""))
    if dry: return
    rec = (r.get("recording") or "").strip()
    if not rec or not os.path.exists(rec):
        print(f"  no recording yet ({rec or 'blank'}), skipping"); return
    aspects = ["9x16", "16x9"] if (r.get("aspect") or "").strip() == "both" else [(r.get("aspect") or "9x16").strip()]
    outs = [os.path.join(out_dir, f"{name}_{asp}.mp4") for asp in aspects]
    if all(os.path.exists(o) for o in outs):
        print("  already rendered"); return
    words = os.path.join(work, f"{name}.words.json")
    if not os.path.exists(words):
        subprocess.run([sys.executable, os.path.join(HERE, "align.py"), rec, r["passage"], words], check=True)
    if not yes(r.get("blend", "")) and (r.get("fit") or "span").strip() in ("", "span"):
        reading = json.load(open(words))[-1]["end"] + 0.3     # the ending starts here; the span plays across it
        reading += intro.lead_for((r.get("intro") or "").strip(), (r.get("lead") or "").strip())   # after the opener
        need = int(reading * 30) + 1
        if len(rows) < need:
            rows = widen(db, rows, need)
            print(f"  span widened to {len(rows)} frames ({rows[0][2][:16]} to {rows[-1][2][:16]}) so every frame "
                  "plays once across the reading")
    cache = os.path.join(work, "frames", name)
    frame_txt = os.path.join(work, f"{name}.frames.txt")
    open(frame_txt, "w").write("\n".join(materialize(rows, cache)) + "\n")
    for asp, out in zip(aspects, outs):
        if os.path.exists(out): continue
        cmd = [sys.executable, os.path.join(HERE, "render.py"), frame_txt, out, "--aspect", asp,
               "--words", words, "--text", r["passage"], "--voice", rec, "--threads", str(threads)]
        for col, flag in [("move", "--move"), ("style", "--style"), ("fit", "--fit"), ("ending", "--ending"),
                          ("music", "--music"), ("music_start", "--music-start"), ("music_db", "--music-db"),
                          ("end_boost_db", "--end-boost-db"), ("trails", "--trails"), ("exposure", "--exposure"),
                          ("beat_punch", "--beat-punch"), ("fx_every", "--fx-every"), ("end_card", "--end-card"),
                          ("end_card_seconds", "--end-card-seconds"), ("end_card_sound", "--end-card-sound"),
                          ("cover", "--cover"), ("safe", "--safe"), ("contrast", "--contrast"),
                          ("intro", "--intro"), ("intro_sound", "--intro-sound"), ("intro_db", "--intro-db"),
                          ("intro_seconds", "--intro-seconds"), ("lead", "--lead"), ("rewind_sfx", "--rewind-sfx"),
                          ("voice_db", "--voice-db"), ("voice_clean", "--voice-clean"), ("loudness", "--loudness")]:
            if (r.get(col) or "").strip(): cmd += [flag, r[col].strip()]
        if yes(r.get("beat_rewind", "")): cmd.append("--beat-rewind")
        if yes(r.get("blend", "")): cmd.append("--blend")
        subprocess.run(cmd, check=True)
    if not keep_frames: shutil.rmtree(cache, ignore_errors=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet"); ap.add_argument("--only", nargs="+"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=OUT, help="where finished clips go (default: CORSAIR/_captions)")
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--keep-frames", action="store_true", help="keep the unpacked frames after rendering")
    ap.add_argument("--threads", type=int, default=2, help="threads per render, at low priority (0 = full speed)")
    a = ap.parse_args()
    if a.threads:
        try: os.nice(10)                          # renders inherit it; render.py adds background QoS on macOS
        except OSError: pass
    db = sqlite3.connect(a.db)
    for r in csv.DictReader(open(a.sheet, newline="", encoding="utf-8")):
        if not (r.get("name") or "").strip() or r["name"].startswith("#"): continue
        if a.only and r["name"].strip() not in a.only: continue
        run_row(db, r, a.out, a.dry_run, a.keep_frames, a.threads)
