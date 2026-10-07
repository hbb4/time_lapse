# pipeline

Phase 1: index, proxies and per-frame metrics. Phase 2: ranked one-day clips from
`recipes.toml`, reviewed on a local page.
Plan: https://claude.ai/code/artifact/b05bda61-1cae-41aa-b7d1-2749f4e9c919

Everything is written to `/Volumes/CORSAIR/ATLI_videos_and_stills/_pipeline/`:

- `pipeline.db`: SQLite. `frames` has a row per frame (EXIF time, sun elevation and
  azimuth, brightness and percentiles, sky colour and saturation, frame-to-frame
  change, camera shift, sharpness); `folders` lists the folders analysed.
- `proxies/<folder>.tar`: upright 380x672 JPEGs, about 28 KB each, one tar per folder
  (single files would waste most of a 512 KB exFAT block each).
- `previews/`: quick preview videos made from the proxies.
- `samples/<recipe>/`: half-size clip samples from the proxies, for review.
- `renders/<recipe>/`: full-resolution 1080x1920 (or 1920x1080) clips from the original frames.

Setup, once, from the repo root (Python packages stay in the project's .venv;
ffmpeg and djxl come from Homebrew):

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

Run from the repo root:

    .venv/bin/python -m pipeline.analyze --folders TLST05B01403_251014111254 ... --sequence-id 4
    .venv/bin/python -m pipeline.analyze --sequence 4   # needs pipeline/frame_folders.csv
    .venv/bin/python -m pipeline.analyze --status
    .venv/bin/python -m pipeline.preview --start "2025-10-15 05:30" --end "2025-10-15 20:00" --deflicker

Folder runs are resumable: finished folders are skipped.

Notes:

- Reads loose folders and `pack_frames.py` tars alike; a finished tar wins over a loose folder.
- If `reclaim_space.sh` deletes a folder's originals while it is being read, that folder
  is left unfinished and the next run redoes it from the tar.
- Shifts (`dx_*`, `dy_*`) are in proxy pixels of the upright frame (x4 for full resolution);
  `*_prev` is against the previous frame, `*_ref` against the run's first daytime frame.
  `peak_*` near 1 means a confident match; night frames are low.
- Sky is the top 40% of the upright frame and stabilization uses the bottom 45%
  (`config.py`).

## Camera clock

The camera's clock does not follow daylight saving and only changes when the camera
is restarted. `pipeline.clock` measures its offset from the images (when the sky
brightens and darkens each day against civil dawn and dusk) and corrects the index;
the camera's own time stays in `frames.t_camera`. `analyze` uses the measured
offsets for new folders, so scan before analysing footage from a new period.

    .venv/bin/python -m pipeline.clock scan     # sample every folder (resumable), then print offsets
    .venv/bin/python -m pipeline.clock show
    .venv/bin/python -m pipeline.clock apply    # correct times and sun positions; then re-plan clips

Folders listed in `EXCLUDE_FOLDERS` (`config.py`) are indoor time lapses, not the
window view; analysis, the clock estimate and clips all skip them.

## Clips (phase 2)

`recipes.toml` at the repo root describes each kind of clip: its time window
(anchored to dawn, sunrise, noon, sunset or dusk that day, or a clock time), speed,
how days are scored, aspect and pan. Edit it and re-plan; keep/reject choices survive.

    .venv/bin/python -m pipeline.clips plan               # score and rank every day for every recipe
    .venv/bin/python -m pipeline.clips list --recipe sunset
    .venv/bin/python -m pipeline.clips samples            # quick samples of the top 5 days per recipe
    .venv/bin/python -m pipeline.review                   # review page at http://127.0.0.1:8765
    .venv/bin/python -m pipeline.clips render --status keep   # full resolution for the ones you kept

Scores are 0-100, ranked within each recipe (100 = best day of the footage so far).
`--top N`, `--recipe`, `--id` and `--redo` narrow or repeat a run. Days without
footage across most of a recipe's window are skipped.

By default a clip plays every frame the camera shot, one per video frame at 30 fps,
so the folder's shot interval (5 to 15 s) sets the pace. Cross-faded in-between
frames are only made when a recipe sets `blend = true`. The `clouds` score needs sky structure, which `plan`
measures from the proxies on first use (the `sky` table).

Variable speed: with `max_speedup` above 1, stretches where the sky barely changes
play faster (up to that factor), easing in and out over about 2 s of video; anything
with clouds moving plays at the recipe's normal speed.
