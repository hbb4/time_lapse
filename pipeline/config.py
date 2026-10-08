"""Shared settings for the time lapse pipeline."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

FRAMES_ROOT = Path(os.environ.get("TL_FRAMES_ROOT", "/Volumes/CORSAIR/ATLI_videos_and_stills"))
PACKED_DIRS = [FRAMES_ROOT / "_packed", Path.home() / "time_lapse_packed"]
# Index, proxies and previews all live on CORSAIR next to the frames.
OUT_ROOT = Path(os.environ.get("TL_OUT_ROOT", str(FRAMES_ROOT / "_pipeline")))
DB_PATH = OUT_ROOT / "pipeline.db"
PROXY_DIR = OUT_ROOT / "proxies"
PREVIEW_DIR = OUT_ROOT / "previews"
INVENTORY_CSV = Path(__file__).with_name("frame_folders.csv")

# Indoor time lapses (plants), not the window view: left out of analysis, the clock
# estimate and clips. Nothing is deleted; these folders are just skipped.
EXCLUDE_FOLDERS = {
    "TLST05B01403_250607094832",  # 2025-06-07 to 06-10, cactus on a table
    "TLST05B01403_250613023928",  # 2025-06-13 to 09-05, grow tent, one frame per 15 minutes
}


def not_excluded(column="folder"):
    """SQL condition that leaves out frames from EXCLUDE_FOLDERS."""
    if not EXCLUDE_FOLDERS:
        return "1"
    return f"{column} NOT IN ({', '.join(repr(f) for f in sorted(EXCLUDE_FOLDERS))})"

# Camera is mounted on its side: raw frames are 2688x1520 with the sky on the left.
# Rotating 90 degrees clockwise gives an upright 1520x2688 frame.
ROTATE_CW = True
# Folders shot with the camera's own rotation on: frames are already upright 1520x2688.
UPRIGHT_FOLDERS = {
    "TLST05B01403_250924074942",  # 2025-09-24 to 09-29
}
PROXY_SCALE = 4          # JPEG draft decode at 1/4: 380x672 upright
PROXY_QUALITY = 85
SKY_FRACTION = 0.40      # top 40% of the upright frame is sky
GROUND_FRACTION = 0.45   # bottom 45% (buildings) is used for stabilization

TZ = ZoneInfo("America/Los_Angeles")
LATITUDE = 37.791667734079596
LONGITUDE = -122.41549323195979
