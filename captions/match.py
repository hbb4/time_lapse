"""Pair passages with footage and write a clip sheet for batch.py.

  python match.py captions.csv                       -> matched_clips.csv + passages/p###.txt
  python match.py captions.csv --only 12 40 41       just these passage indexes
  python match.py captions.csv --long-seconds 90     readings longer than this become 16:9 long form

How a passage gets its footage:
  1. Length. The reading time is estimated from the word count (about 140 words a minute, a little
     longer at verse line and stanza breaks). Short readings become 9:16 clips; long ones become
     16:9 long form.
  2. Mood. Words in the passage vote for a clip type: evening/endings/rest -> sunset; morning/beginnings/
     hope -> burnoff or dawn_to_midday; night/stars/silence -> night; work/struggle/change/weather ->
     midday; life/years/time -> a long multi-day stretch. The "why" column shows the words that decided it.
  3. Day. From the clips you ranked in the pipeline (pipeline.db), it takes the best one of that type,
     preferring ones you marked keep, never ones you rejected, and uses each day once before repeating.
     About 30% of passages (--any) ignore the mood and take any kind of footage, so the weather doesn't
     always have to match the words.

Short clips use the clip's window and stretch it across the reading. Long form plays every frame once
(30 fps, the camera's own smoothness), so a longer reading covers more real time: the span grows from
the clip's anchor (it ends at the sunset window's end, starts at dawn or night windows' start, or
centers on midday) until it is long enough, crossing into neighbouring days and folders as needed.

Everything here is a suggestion: edit the sheet (swap days, styles, moves, add *stress* and {spots} to the
passage files) before running batch.py. The recording column names where batch.py expects your reading;
rows render once that file exists.
"""
import argparse, csv, os, random, re, sqlite3, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # time_lapse repo root
try:
    from pipeline import config
    DB, EXCLUDE = str(config.DB_PATH), sorted(getattr(config, "EXCLUDE_FOLDERS", []))
except ImportError:                       # running outside the repo (e.g. a test copy)
    DB, EXCLUDE = "/Volumes/CORSAIR/ATLI_videos_and_stills/_pipeline/pipeline.db", []
OK = "error IS NULL" + (f" AND folder NOT IN ({','.join(repr(f) for f in EXCLUDE)})" if EXCLUDE else "")

MOODS = {   # clip type -> words that point to it (matched on word stems)
    "sunset": "evening sunset dusk twilight golden glow end ending close last fade farewell goodbye gone go "
              "rest done finish old age autumn memory remember past ember warm home return late",
    "burnoff": "morning dawn sunrise begin beginning start new rise wake awaken hope tomorrow young youth fresh "
               "clear clearing open dream first spring born birth reveal lift",
    "night": "night dark darkness star stars moon sleep lonely alone silence silent quiet still fear shadow "
             "deep mystery prayer soul solitude",
    "midday": "work labor effort strive struggle fight try climb road journey path persist courage strength "
              "change wind cloud clouds storm weather busy duty task win lose battle hard",
    "long": "life lives years year time ages generations history forever always eternity grow growing world "
            "journey season seasons patience lifetime",
}
STEMS = {k: {w[:5] for w in v.split()} for k, v in MOODS.items()}
RECIPE_FOR = {"sunset": ["sunset"], "burnoff": ["burnoff", "dawn_to_midday"], "night": ["night"],
              "midday": ["midday"], "long": ["sunset", "midday", "burnoff", "dawn_to_midday"]}
ANCHOR = {"sunset": "end", "burnoff": "start", "dawn_to_midday": "start", "night": "start", "midday": "center"}
STYLES = ["pop", "serif", "type", "whisper", "poster"]

def reading_seconds(text, wpm=140):
    words = len([w for w in text.split() if re.search(r"\w", w)])
    lines = text.count("\n") - 2 * text.count("\n\n")
    return words / wpm * 60 + 0.25 * max(0, lines) + 0.6 * text.count("\n\n")

def mood(text, long_form):
    counts, hits = Counter(), {}
    for w in re.findall(r"[a-z’']+", text.lower()):
        for k, st in STEMS.items():
            if k == "long" and not long_form: continue     # "life", "time"... only steer long readings
            if w[:5] in st:
                counts[k] += 1; hits.setdefault(k, []).append(w)
    if long_form: counts["long"] += 1          # long readings lean toward long stretches
    if not counts: return "midday", "no mood words; default"
    k = counts.most_common(1)[0][0]
    return k, ", ".join(sorted(set(hits.get(k, ["long reading"])))[:6])

def load_clips(db):
    rows = db.execute("SELECT id, recipe, day, start_local, end_local, score, status FROM clips "
                      "WHERE status != 'reject' ORDER BY (status = 'keep') DESC, score DESC").fetchall()
    by = {}
    for r in rows: by.setdefault(r[1], []).append(dict(zip(["id", "recipe", "day", "start", "end", "score", "status"], r)))
    return by

def frames_between(db, t0, t1):
    return db.execute(f"SELECT folder, n, t_local FROM frames WHERE {OK} AND t_local >= ? AND t_local <= ? "
                      "ORDER BY t_local", (t0, t1)).fetchall()

def long_span(db, clip, need):
    """`need` consecutive frames anchored on the clip window (see module notes)."""
    anchor = ANCHOR.get(clip["recipe"], "center")
    win = frames_between(db, clip["start"], clip["end"])
    if anchor == "end":
        before = db.execute(f"SELECT folder, n, t_local FROM frames WHERE {OK} AND t_local <= ? "
                            "ORDER BY t_local DESC LIMIT ?", (clip["end"], need)).fetchall()[::-1]
        return before
    if anchor == "start":
        return db.execute(f"SELECT folder, n, t_local FROM frames WHERE {OK} AND t_local >= ? "
                          "ORDER BY t_local LIMIT ?", (clip["start"], need)).fetchall()
    mid = win[len(win) // 2][2] if win else clip["start"]
    a = db.execute(f"SELECT folder, n, t_local FROM frames WHERE {OK} AND t_local < ? "
                   "ORDER BY t_local DESC LIMIT ?", (mid, need // 2)).fetchall()[::-1]
    b = db.execute(f"SELECT folder, n, t_local FROM frames WHERE {OK} AND t_local >= ? "
                   "ORDER BY t_local LIMIT ?", (mid, need - len(a))).fetchall()
    return a + b

def span_fields(span):
    """from / to as clock-corrected date-times (batch.py looks the frames up again in pipeline.db)."""
    return span[0][2][:16], span[-1][2][:16]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("captions")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default="matched_clips.csv")
    ap.add_argument("--passages", default="passages", help="folder for the passage text files")
    ap.add_argument("--only", nargs="+", type=int, help="passage indexes")
    ap.add_argument("--long-seconds", type=float, default=75, help="readings longer than this become 16:9 long form")
    ap.add_argument("--long-speed", type=float, default=1.0,
                    help="long form: real frames per video frame (1 = every frame once; 2 covers twice the time)")
    ap.add_argument("--wpm", type=float, default=140, help="your reading pace, words per minute")
    ap.add_argument("--music", default="", help="music file to put on every row (optional)")
    ap.add_argument("--any", type=float, default=0.3,
                    help="share of passages that get footage of any kind regardless of mood (0-1, default 0.3)")
    ap.add_argument("--seed", type=int, default=1, help="change to reshuffle the --any picks and styles")
    a = ap.parse_args()

    db = sqlite3.connect(a.db) if os.path.exists(a.db) else None
    if not db: print(f"no {a.db}: writing lengths and moods only (from/to left blank)")
    clips = load_clips(db) if db else {}
    used = Counter()
    rnd = random.Random(a.seed)
    os.makedirs(a.passages, exist_ok=True)
    cols = ["name", "passage", "recording", "folder", "from", "to", "move", "style", "aspect", "fit", "ending",
            "music", "music_start", "music_db", "end_boost_db", "trails", "exposure", "beat_punch", "beat_rewind",
            "fx_every", "end_card", "end_card_seconds", "end_card_sound", "cover", "safe", "contrast", "blend", "index", "author", "title", "words", "est_seconds", "mood", "why", "recipe", "day", "day_score", "span"]
    out = []
    for r in csv.DictReader(open(a.captions, encoding="utf-8")):
        i = int(r["index"])
        if a.only and i not in a.only: continue
        text = r["text"]
        secs = reading_seconds(text, a.wpm)
        long_form = secs > a.long_seconds
        m, why = mood(text, long_form)
        pfile = os.path.join(a.passages, f"p{i:03d}.txt")
        if not os.path.exists(pfile):                 # never overwrite a file you've marked up
            open(pfile, "w", encoding="utf-8").write(text + "\n")
        row = dict(name=f"p{i:03d}_{m}", passage=pfile, recording=f"recordings/p{i:03d}.m4a",
                   move="drift" if long_form else "push", style="",
                   aspect="16x9" if long_form else "9x16", fit="realtime" if long_form and a.long_speed == 1 else "",
                   ending="rewind", music=a.music, index=i, author=r["author"], title=r["title"],
                   words=r["word_count"], est_seconds=round(secs), mood=m, why=why)
        free = rnd.random() < a.any                          # this one may use any footage
        recs = sorted(clips, key=lambda k: rnd.random()) if free else RECIPE_FOR[m]
        cands = [c for rec in recs for c in clips.get(rec, [])]   # best first, in recipe order
        pick = min(cands, key=lambda c: used[c["id"]]) if cands else None   # least-used; ties keep the best
        if pick:
            used[pick["id"]] += 1
            if long_form:
                need = int((secs + 3) * 30 * a.long_speed)
                span = long_span(db, pick, need)
            else:
                span = frames_between(db, pick["start"], pick["end"])
            if span:
                f0, f1 = span_fields(span)
                row.update(**{"from": f0, "to": f1}, recipe=pick["recipe"], day=pick["day"], name=f"p{i:03d}_{pick['recipe']}",
                           day_score=round(pick["score"] or 0), span=f"{len(span)} frames",
                           why=row["why"] + ("; footage picked freely" if free else ""))
        out.append(row)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(out)
    n_long = sum(1 for r in out if r["aspect"] == "16x9")
    print(f"wrote {len(out)} rows to {a.out} ({n_long} long form, {len(out) - n_long} vertical); "
          f"moods: {dict(Counter(r['mood'] for r in out))}")

if __name__ == "__main__":
    main()
