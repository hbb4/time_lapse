# pipeline

Phase 1 of the processing pipeline: index, proxies and per-frame metrics.
Plan: https://claude.ai/code/artifact/b05bda61-1cae-41aa-b7d1-2749f4e9c919

Everything is written to `/Volumes/CORSAIR/ATLI_videos_and_stills/_pipeline/`:

- `pipeline.db`: SQLite. `frames` has a row per frame (EXIF time, sun elevation and
  azimuth, brightness and percentiles, sky colour and saturation, frame-to-frame
  change, camera shift, sharpness); `folders` lists the folders analysed.
- `proxies/<folder>.tar`: upright 380x672 JPEGs, about 28 KB each, one tar per folder
  (single files would waste most of a 512 KB exFAT block each).
- `previews/`: quick preview videos made from the proxies.

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
