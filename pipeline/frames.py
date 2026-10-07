"""Read frames from a camera folder whether it is still loose JPEGs or packed by pack_frames.py.

A packed folder is a tar of TLS_*.jxl (lossless JPEG XL) or, where cjxl failed, TLS_*.jpg.
Frames are yielded in frame order as (number, kind, data):
  kind "path": data is the path of a loose .jpg
  kind "jpg":  data is the JPEG bytes
  kind "jxl":  data is the JPEG XL bytes (decode with jpeg_bytes())
"""

import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .config import FRAMES_ROOT, PACKED_DIRS

FRAME_RE = re.compile(r"^TLS_(\d{9})\.(jxl|jpg)$")


def locate(name):
    """Return ("tar", path) or ("loose", path) for a folder name, or raise FileNotFoundError.

    A finished tar wins: pack_frames.py only deletes originals after the tar verifies,
    so a loose folder next to a finished tar may already be partly deleted.
    """
    for d in PACKED_DIRS:
        tar = d / f"{name}.tar"
        if tar.is_file():
            return "tar", tar
    loose = FRAMES_ROOT / name
    if loose.is_dir() and any(loose.glob("TLS_*.jpg")):
        return "loose", loose
    raise FileNotFoundError(f"{name}: no packed tar in {', '.join(map(str, PACKED_DIRS))} "
                            f"and no frames in {loose}")


def iter_frames(kind, path, start=None, end=None):
    def wanted(n):
        return (start is None or n >= start) and (end is None or n <= end)

    if kind == "loose":
        entries = []
        with os.scandir(path) as it:
            for e in it:
                m = FRAME_RE.match(e.name)
                if m and m.group(2) == "jpg" and not e.name.startswith("._"):
                    entries.append((int(m.group(1)), e.path))
        for n, p in sorted(entries):
            if wanted(n):
                yield n, "path", p
        return

    # Stream the tar front to back: frames were written in order, and a streaming
    # read never seeks, which keeps USB reads sequential.
    with tarfile.open(path, "r|") as tar:
        for m in tar:
            parts = m.name.split("/")
            if len(parts) != 2 or not m.isfile():
                continue
            match = FRAME_RE.match(parts[1])
            if not match:
                continue
            n = int(match.group(1))
            if not wanted(n):
                if end is not None and n > end:
                    break
                continue
            yield n, match.group(2), tar.extractfile(m).read()


def jpeg_bytes(kind, data):
    """Return the original JPEG bytes for one yielded frame."""
    if kind == "path":
        with open(data, "rb") as f:
            return f.read()
    if kind == "jpg":
        return data
    with tempfile.TemporaryDirectory(prefix="tl_jxl_") as tmp:
        src, dst = Path(tmp) / "f.jxl", Path(tmp) / "f.jpg"
        src.write_bytes(data)
        r = subprocess.run(["djxl", "--quiet", str(src), str(dst)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"djxl failed: {(r.stderr or r.stdout).strip()[-200:]}")
        return dst.read_bytes()
