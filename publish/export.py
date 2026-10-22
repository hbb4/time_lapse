"""Probe a video, decide which platforms it fits, and make one upload-ready copy of it."""
import hashlib, json, subprocess
from pathlib import Path

from . import config
from .models import Media

def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True)
    if r.returncode: raise SystemExit(f"ffprobe could not read {path}: {r.stderr.strip()}")
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    if not v: raise SystemExit(f"{path} has no video")
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    w, h = int(v["width"]), int(v["height"])
    rot = int(float(v.get("tags", {}).get("rotate", 0) or 0))
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd: rot = int(float(sd["rotation"]))
    if abs(rot) % 180 == 90: w, h = h, w
    num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0.0
    return Media(Path(path), w, h, float(j["format"].get("duration") or 0), fps, v["codec_name"], a["codec_name"] if a else None,
                 v.get("pix_fmt", ""), int(j["format"].get("size") or Path(path).stat().st_size))

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""): h.update(block)
    return h.hexdigest()

def route(media, wanted, settings):
    """{platform: (kind, None)} for platforms that fit, {platform: (None, reason)} for ones that don't."""
    lim, out = settings["limits"], {}
    d = media.duration
    for p in wanted:
        L = lim[p]
        if d > L["max_seconds"]:
            out[p] = (None, f"{d:.0f}s is over the {L['max_seconds']}s limit (raise limits.{p}.max_seconds in settings.toml if your account allows more)")
        elif media.bytes > L["max_bytes"]:
            out[p] = (None, f"{media.bytes / 2**20:.0f} MB is over the {L['max_bytes'] / 2**20:.0f} MB limit")
        elif p == "youtube":
            out[p] = ("short" if media.vertical and d <= L["short_seconds"] else "long", None)
        elif p == "instagram":
            out[p] = ("reel", None) if media.vertical else (None, "Reels are vertical; this video is landscape")
        elif p == "facebook":
            out[p] = ("reel" if media.vertical else "video", None)
        elif p == "x":
            out[p] = ("post", None)
        else:
            out[p] = ("post", None)
    return out

def compliant(m):
    """Already in the one format every platform accepts (H.264, yuv420p, AAC, 60 fps or less, 1080-class size)."""
    return (m.vcodec == "h264" and m.pix_fmt == "yuv420p" and (m.acodec in (None, "aac")) and 0 < m.fps <= 60.5
            and max(m.width, m.height) <= 1920 and m.path.suffix.lower() in (".mp4", ".mov", ".m4v"))

def universal(path, media, out_dir=None):
    """One upload-ready file: the video itself (remuxed with the index up front) when it is already compliant,
    otherwise a high-quality H.264/AAC re-encode. Returns (path, Media)."""
    out_dir = Path(out_dir or config.WORK_DIR); out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (Path(path).stem + ".upload.mp4")
    if compliant(media):
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-c", "copy", "-movflags", "+faststart", str(out)]
    else:
        scale = "scale=w=1920:h=1920:force_original_aspect_ratio=decrease:force_divisible_by=2"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", f"{scale},fps=fps=min(60\\,source_fps)",
               "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-maxrate", "16M", "-bufsize", "32M", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return out, probe(out)

def preview(src, out, max_bytes=45 * 2**20):
    """A copy small enough to send to a phone (Telegram bots take up to 50 MB): the file itself when it already is, else a 960 px, CRF 30 re-encode."""
    src = Path(src)
    if src.stat().st_size <= max_bytes: return src
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=w=960:h=960:force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-maxrate", "1500k", "-bufsize", "3M", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)], check=True)
    return Path(out)
