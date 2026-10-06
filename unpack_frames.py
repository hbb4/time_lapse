#!/usr/bin/env python3
"""
Restore original TLS_*.jpg frames from a tar made by pack_frames.py, so the
video scripts (which read a folder of TLS_%09d.jpg) work on packed folders.

Frames keep their original names and numbers, and each restored file is checked
against the sha256 recorded when it was packed. The last line printed is the
folder holding the frames, so it can be captured in a shell script:

  FRAMES=$(./unpack_frames.py TLST05B01403_250907184453 --start 200 --end 800 | tail -1)
  ./make_timelapse.sh "$FRAMES" out.mp4 200 800 30 none

Usage:
  unpack_frames.py NAME_OR_TAR [--start N] [--end M] [--out DIR]
  unpack_frames.py NAME_OR_TAR --list

NAME_OR_TAR is a path to a .tar, a folder name such as TLST05B01403_250907184453,
or the old folder path (its packed tar is looked up by name).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pack_frames import SUMS_NAME, sha256_file

PACKED_DIRS = [
    Path("/Volumes/CORSAIR/ATLI_videos_and_stills/_packed"),
    Path.home() / "time_lapse_packed",
]
DEFAULT_OUT = Path.home() / "time_lapse_packed" / "unpacked"
FRAME_RE = re.compile(r"^TLS_(\d{9})\.(jxl|jpg)$")


def find_tar(arg, packed_dirs=PACKED_DIRS):
    """Resolve a tar path, folder name or old folder path to a packed tar, or None."""
    p = Path(arg).expanduser()
    if p.suffix == ".tar":
        return p if p.is_file() else None
    for d in packed_dirs:
        candidate = d / f"{p.name}.tar"
        if candidate.is_file():
            return candidate
    return None


def frame_members(tar):
    """Return {frame_number: TarInfo} for the top-level frames in an open tar."""
    frames = {}
    for m in tar.getmembers():
        parts = m.name.split("/")
        if len(parts) == 2 and (match := FRAME_RE.match(parts[1])):
            frames[int(match.group(1))] = m
    return frames


def read_sums(tar):
    name = tar.getmembers()[0].name.split("/", 1)[0]
    sums = {}
    for line in tar.extractfile(f"{name}/{SUMS_NAME}").read().decode().splitlines():
        digest, rel = line.split("  ", 1)
        sums[rel] = digest
    return sums


def _restore_one(args):
    jxl, jpg, digest = args
    tmp = jpg.with_name(f".part_{jpg.name}")  # djxl picks the format from the extension
    try:
        if jxl.suffix == ".jxl":
            r = subprocess.run(["djxl", "--quiet", str(jxl), str(tmp)], capture_output=True, text=True)
            if r.returncode != 0:
                return f"{jpg.name}: djxl failed: {(r.stderr or r.stdout).strip()[-200:]}"
        else:
            # pack_frames.py stores the jpg as-is when cjxl could not transcode it
            jxl.rename(tmp)
        if sha256_file(tmp) != digest:
            return f"{jpg.name}: sha256 mismatch after restore"
        tmp.rename(jpg)
        return None
    finally:
        jxl.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


def restore(tar_path, out_dir, start=None, end=None, workers=None):
    """Restore frames start..end (inclusive, original numbering) into out_dir.

    Frames already present in out_dir are kept. Returns the number restored.
    """
    workers = workers or os.cpu_count() or 4
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    restored = 0
    with tarfile.open(tar_path) as tar, ThreadPoolExecutor(workers) as pool:
        frames = frame_members(tar)
        sums = read_sums(tar)
        wanted = [n for n in sorted(frames)
                  if (start is None or n >= start) and (end is None or n <= end)]
        if not wanted:
            sys.exit(f"no frames in range in {tar_path} (it holds {min(frames)}..{max(frames)})")
        pending = []

        def drain(limit):
            nonlocal restored
            while len(pending) > limit:
                err = pending.pop(0).result()
                if err:
                    errors.append(err)
                else:
                    restored += 1

        for n in wanted:
            jpg = out_dir / f"TLS_{n:09d}.jpg"
            if jpg.exists():
                continue
            m = frames[n]
            staged = out_dir / f".{Path(m.name).name}"
            with open(staged, "wb") as f:
                shutil.copyfileobj(tar.extractfile(m), f)
            pending.append(pool.submit(_restore_one, (staged, jpg, sums[jpg.name])))
            drain(workers * 2)
        drain(0)
    if errors:
        for e in errors[:20]:
            print(f"  FAIL {e}", file=sys.stderr)
        sys.exit(f"{len(errors)} frames failed to restore")
    return restored, len(wanted)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="tar path, folder name, or old folder path")
    ap.add_argument("--start", type=int, help="first frame number (default: first in tar)")
    ap.add_argument("--end", type=int, help="last frame number, inclusive (default: last in tar)")
    ap.add_argument("--out", type=Path, help=f"destination folder (default: {DEFAULT_OUT}/<name>)")
    ap.add_argument("--list", action="store_true", help="print the frame range in the tar and exit")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    args = ap.parse_args()

    tar_path = find_tar(args.source)
    if tar_path is None:
        sys.exit(f"no packed tar found for {args.source} (looked in {', '.join(map(str, PACKED_DIRS))})")
    name = tar_path.stem

    if args.list:
        with tarfile.open(tar_path) as tar:
            frames = frame_members(tar)
        print(f"{tar_path}: {len(frames)} frames, {min(frames)}..{max(frames)}")
        return

    if not shutil.which("djxl"):
        sys.exit("djxl not found on PATH (brew install jpeg-xl)")
    out_dir = (args.out or DEFAULT_OUT / name).expanduser()
    restored, wanted = restore(tar_path, out_dir, args.start, args.end, args.workers)
    print(f"restored {restored} frames from {tar_path} ({wanted - restored} already present)")
    print(out_dir)


if __name__ == "__main__":
    main()
