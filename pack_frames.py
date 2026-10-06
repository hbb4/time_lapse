#!/usr/bin/env python3
"""
Pack one time-lapse frame folder into a single uncompressed .tar of lossless
JPEG XL files, to recover space on drives with a large allocation block.

For a folder such as .../TLST05B01403_250907184453 this:
  1. hashes every original (sha256) and transcodes each TLS_*.jpg to .jxl with
     `cjxl --lossless_jpeg=1` (bit-exact JPEG reconstruction);
  2. writes <out-dir>/<folder>.tar holding the .jxl frames, the thumbnail/
     files, the note, and SHA256SUMS listing the originals;
  3. verifies the tar: every .jxl is restored with djxl to a temp location and
     its sha256 compared to the original's; every other member is hashed too;
  4. only with --delete-originals, and only after a fully passing verification,
     re-hashes each original and deletes it.

Usage:
  pack_frames.py FOLDER --out-dir ~/time_lapse_packed
  pack_frames.py --verify-only ~/time_lapse_packed/FOLDER.tar
  pack_frames.py FOLDER --existing-tar FOLDER.tar --delete-originals   (resume)

Exit codes: 0 ok, 1 verification or other failure, 3 not enough free space.

Restore a frame by hand:
  tar -xf FOLDER.tar FOLDER/TLS_000000001.jxl && djxl FOLDER/TLS_000000001.jxl TLS_000000001.jpg
"""

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SUMS_NAME = "SHA256SUMS"
EXIT_NO_SPACE = 3  # distinct so a driver can fall back to another --out-dir
FRAME_GLOB = "TLS_*.jpg"


def sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def sha256_stream(f, bufsize=1 << 20):
    h = hashlib.sha256()
    while chunk := f.read(bufsize):
        h.update(chunk)
    return h.hexdigest()


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def collect(folder):
    """Return (frames, extras) as sorted lists of paths relative to folder."""
    frames = sorted(p.name for p in folder.glob(FRAME_GLOB) if p.is_file())
    frame_set = set(frames)
    extras = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            # ._* are AppleDouble sidecars macOS writes on ExFAT, not camera data
            if name.startswith("._") or name == ".DS_Store":
                continue
            rel = os.path.relpath(os.path.join(root, name), folder)
            if rel not in frame_set:
                extras.append(rel)
    return frames, extras


def transcode_one(args):
    """Hash the original and transcode it. Returns (rel, sha, staged_path|None, error|None)."""
    folder, rel, stage, effort = args
    src = folder / rel
    digest = sha256_file(src)
    dst = stage / (Path(rel).stem + ".jxl")
    r = subprocess.run(
        ["cjxl", "--lossless_jpeg=1", "-e", str(effort), "--quiet", str(src), str(dst)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        return rel, digest, None, (r.stderr or r.stdout).strip()[-200:]
    return rel, digest, dst, None


def add_bytes(tar, arcname, data, mtime):
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = mtime
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def add_file(tar, arcname, path, mtime=None):
    st = os.stat(path)
    info = tarfile.TarInfo(arcname)
    info.size = st.st_size
    info.mtime = int(st.st_mtime if mtime is None else mtime)
    info.mode = 0o644
    with open(path, "rb") as f:
        tar.addfile(info, f)


class NoSpace(Exception):
    pass


def build(folder, tar_path, tmp_root, workers, effort, min_free):
    name = folder.name
    frames, extras = collect(folder)
    if not frames:
        sys.exit(f"no {FRAME_GLOB} files in {folder}")
    orig_bytes = sum((folder / r).stat().st_size for r in frames + extras)
    print(f"{name}: {len(frames)} frames, {len(extras)} other files, {human(orig_bytes)}")

    # The tar's size isn't known until it is written; the originals' size is a safe upper bound.
    free = shutil.disk_usage(tar_path.parent).free
    if free < orig_bytes + min_free:
        raise NoSpace(f"not enough space in {tar_path.parent}: {human(free)} free, "
                      f"need up to {human(orig_bytes)} + {human(min_free)} margin")

    stage = Path(tempfile.mkdtemp(prefix=f"pack_{name}_", dir=tmp_root))
    partial = tar_path.with_name(tar_path.name + ".partial")
    sums = {}  # rel path of original -> sha256
    t0 = time.time()
    try:
        with tarfile.open(partial, "w", format=tarfile.PAX_FORMAT) as tar, \
                ThreadPoolExecutor(workers) as pool:
            for rel in extras:
                sums[rel] = sha256_file(folder / rel)
                add_file(tar, f"{name}/{rel}", folder / rel)

            # Frames go into the tar in order as they finish, so only a few
            # transcoded files exist in the staging directory at any time.
            pending = []
            done = 0

            def drain(limit):
                nonlocal done
                while len(pending) > limit:
                    rel, digest, dst, err = pending.pop(0).result()
                    sums[rel] = digest
                    mtime = (folder / rel).stat().st_mtime
                    if dst is None:
                        print(f"  cjxl failed for {rel}, storing original jpg: {err}")
                        add_file(tar, f"{name}/{rel}", folder / rel)
                    else:
                        add_file(tar, f"{name}/{Path(rel).stem}.jxl", dst, mtime)
                        dst.unlink()
                    done += 1
                    if done % 1000 == 0 or done == len(frames):
                        print(f"  packed {done}/{len(frames)} ({time.time() - t0:.0f}s)", flush=True)

            for rel in frames:
                pending.append(pool.submit(transcode_one, (folder, rel, stage, effort)))
                drain(workers * 2)
            drain(0)

            sums_data = "".join(f"{sums[r]}  {r}\n" for r in frames + extras).encode()
            add_bytes(tar, f"{name}/{SUMS_NAME}", sums_data, int(time.time()))
        return partial, sums
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def read_sums(tar, name):
    f = tar.extractfile(f"{name}/{SUMS_NAME}")
    sums = {}
    for line in f.read().decode().splitlines():
        digest, rel = line.split("  ", 1)
        sums[rel] = digest
    return sums


def restore_and_hash(args):
    """Restore one .jxl (already extracted to jxl_path) with djxl; delete temp files after."""
    jxl_path, jpg_path = args
    try:
        r = subprocess.run(["djxl", "--quiet", str(jxl_path), str(jpg_path)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not jpg_path.exists():
            return None, (r.stderr or r.stdout).strip()[-200:]
        return sha256_file(jpg_path), None
    finally:
        jxl_path.unlink(missing_ok=True)
        jpg_path.unlink(missing_ok=True)


def verify(tar_path, tmp_root, workers, expect_sums=None):
    """Return (ok, passed_frames, total_frames, sums). Temp use is bounded by `workers` frames."""
    tmp = Path(tempfile.mkdtemp(prefix="verify_", dir=tmp_root))
    failures = []
    passed_frames = total_frames = 0
    try:
        with tarfile.open(tar_path, "r") as tar:
            members = [m for m in tar.getmembers() if m.isfile()]
            name = members[0].name.split("/", 1)[0]
            sums = read_sums(tar, name)
            if expect_sums is not None and sums != expect_sums:
                failures.append("SHA256SUMS in tar differs from hashes computed while packing")
            seen = set()

            def check(rel, digest, err=None):
                nonlocal passed_frames
                seen.add(rel)
                is_frame = "/" not in rel and rel.startswith("TLS_")
                if err is None and sums.get(rel) == digest:
                    passed_frames += is_frame
                else:
                    failures.append(f"{rel}: {err or 'sha256 mismatch'}")

            with ThreadPoolExecutor(workers) as pool:
                pending = []

                def drain(limit):
                    while len(pending) > limit:
                        rel, fut = pending.pop(0)
                        digest, err = fut.result()
                        check(rel, digest, err)

                for m in members:
                    inner = m.name.split("/", 1)[1]
                    if inner == SUMS_NAME:
                        continue
                    if inner.endswith(".jxl"):
                        total_frames += 1
                        rel = inner[:-4] + ".jpg"
                        jxl_path = tmp / inner
                        with open(jxl_path, "wb") as out:
                            shutil.copyfileobj(tar.extractfile(m), out)
                        pending.append((rel, pool.submit(restore_and_hash, (jxl_path, tmp / rel))))
                        drain(workers)
                    else:
                        if "/" not in inner and inner.startswith("TLS_"):
                            total_frames += 1
                        check(inner, sha256_stream(tar.extractfile(m)))
                    if total_frames and total_frames % 1000 == 0 and inner.endswith(".jxl"):
                        print(f"  verified {total_frames} frames", flush=True)
                drain(0)
            for rel in sums.keys() - seen:
                failures.append(f"{rel}: listed in {SUMS_NAME} but missing from tar")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for line in failures[:20]:
        print(f"  FAIL {line}")
    if len(failures) > 20:
        print(f"  ... and {len(failures) - 20} more failures")
    return not failures, passed_frames, total_frames, sums


def delete_originals(folder, sums, allow_missing=False):
    """Delete each packed original, re-hashing it first. Stops at the first mismatch."""
    deleted = 0
    for rel, digest in sums.items():
        path = folder / rel
        if allow_missing and not path.exists():
            continue
        if sha256_file(path) != digest:
            sys.exit(f"ABORT: {path} changed since packing; nothing further deleted")
        path.unlink()
        deleted += 1
    for root, dirs, files in os.walk(folder, topdown=False):
        for name in files:
            if name.startswith("._") or name == ".DS_Store":
                os.unlink(os.path.join(root, name))
        try:
            os.rmdir(root)
        except OSError:
            print(f"  left non-empty directory {root}")
    print(f"deleted {deleted} originals from {folder}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, nargs="?", help="frame folder to pack")
    ap.add_argument("--out-dir", type=Path, help="where to write the tar (default: <folder>/../_packed)")
    ap.add_argument("--tmp-dir", type=Path, help="scratch space for staging and verification (default: system temp)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--effort", type=int, default=7, help="cjxl effort (default 7)")
    ap.add_argument("--min-free-mb", type=int, default=1024,
                    help="free space that must remain after writing (default 1024)")
    ap.add_argument("--verify-only", type=Path, metavar="TAR", help="verify an existing tar and exit")
    ap.add_argument("--existing-tar", type=Path, metavar="TAR",
                    help="don't pack; re-verify this tar of FOLDER (for resuming --delete-originals)")
    ap.add_argument("--delete-originals", action="store_true",
                    help="delete the packed originals, only after verification fully passes")
    args = ap.parse_args()

    for tool in ("cjxl", "djxl"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on PATH (brew install jpeg-xl)")
    if args.tmp_dir:
        args.tmp_dir.mkdir(parents=True, exist_ok=True)

    if args.verify_only:
        if args.delete_originals:
            sys.exit("--delete-originals cannot be combined with --verify-only")
        ok, passed, total, _ = verify(args.verify_only, args.tmp_dir, args.workers)
        print(f"verification {'PASSED' if ok else 'FAILED'}: {passed}/{total} frames")
        sys.exit(0 if ok else 1)

    if not args.folder or not args.folder.is_dir():
        ap.error("FOLDER must be an existing directory")
    folder = args.folder.resolve()

    if args.existing_tar:
        # Resume: the tar was packed earlier; originals may already be partly deleted.
        ok, passed, total, sums = verify(args.existing_tar, args.tmp_dir, args.workers)
        print(f"verification {'PASSED' if ok else 'FAILED'}: {passed}/{total} frames")
        with tarfile.open(args.existing_tar) as tar:
            tar_name = tar.next().name.split("/", 1)[0]
        if tar_name != folder.name:
            sys.exit(f"{args.existing_tar} holds {tar_name}, not {folder.name}")
        if not ok:
            sys.exit(1)
        if args.delete_originals:
            delete_originals(folder, sums, allow_missing=True)
        return

    out_dir = (args.out_dir or folder.parent / "_packed").expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    tar_path = out_dir / f"{folder.name}.tar"
    if tar_path.exists():
        sys.exit(f"{tar_path} already exists; refusing to overwrite")

    t0 = time.time()
    try:
        partial, sums = build(folder, tar_path, args.tmp_dir, args.workers, args.effort,
                              args.min_free_mb * 1024 * 1024)
    except NoSpace as e:
        print(e)
        sys.exit(EXIT_NO_SPACE)
    print(f"wrote {partial} ({human(partial.stat().st_size)}) in {time.time() - t0:.0f}s; verifying")

    t1 = time.time()
    ok, passed, total, _ = verify(partial, args.tmp_dir, args.workers, expect_sums=sums)
    print(f"verification {'PASSED' if ok else 'FAILED'}: {passed}/{total} frames in {time.time() - t1:.0f}s")
    if not ok:
        print(f"left unverified archive at {partial}; originals untouched")
        sys.exit(1)

    # A file named .tar only ever exists after a passing verification.
    partial.rename(tar_path)
    print(f"{tar_path}: {human(tar_path.stat().st_size)}, total {time.time() - t0:.0f}s")

    if args.delete_originals:
        delete_originals(folder, sums)


if __name__ == "__main__":
    main()
