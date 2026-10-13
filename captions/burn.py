"""Caption and edit any local video: transcribe, pick a look, burn word-by-word captions in, add the trendy edits.

  .venv/bin/python captions/burn.py in.mp4 out.mp4                         transcribe, auto style, subtle edit
  .venv/bin/python captions/burn.py in.mp4 out.mp4 --edit punchy --cut-silence
  .venv/bin/python captions/burn.py in.mp4 out.mp4 --text passage.txt      known text: its spelling, your timings
  .venv/bin/python captions/burn.py in.mp4 out.mp4 --words words.json      skip transcription (e.g. from align.py)

What it does, in order:
  1. words        Whisper word timings (transcribe.py); with --text they are snapped onto your passage
  2. look         --style auto picks from speech pace, tone and footage brightness (adapt.py); a few punchy words
                  are stressed automatically (--no-emphasis to turn off)
  3. edit         --cut-silence trims dead air between words; --edit sets the zooms (none | subtle | punchy):
                  jump-cut zoom alternating each sentence, a slow push inside each shot, a punch on stressed words
  4. captions     rendered with the same engine as the time-lapse clips (render.py: styles, layout effects,
                  safe area, contrast shadow), on top of the zoomed picture
  5. sound        optional --music ducked under the voice, then loudness set to -14 LUFS (what the platforms target)
  6. extras       cover image (--cover), .srt/.vtt subtitles (--srt), progress bar (--progress)

Output shape follows the source: portrait becomes 1080x1920, landscape 1920x1080 (--aspect 9x16|16x9 crops to fill).
"""
import argparse, bisect, json, math, os, subprocess, sys, tempfile

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adapt, markup, render, styles, transcribe  # noqa: E402

HEADROOM = 1.2            # decode this much larger than the output so zooms up to 1.2x stay sharp
EDITS = {                 # jump-cut zoom, push inside a shot, punch on a stressed word
    "none":   dict(cut=0.0,  push=0.0,  punch=0.0),
    "subtle": dict(cut=0.0,  push=0.04, punch=0.03),
    "punchy": dict(cut=0.09, push=0.02, punch=0.06),
}

def even(x): return int(round(x / 2)) * 2

# ---------- probing ----------
def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                       capture_output=True, text=True)
    if r.returncode: raise SystemExit(f"ffprobe could not read {path}: {r.stderr.strip()}")
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    if not v: raise SystemExit(f"{path} has no video stream")
    w, h = int(v["width"]), int(v["height"])
    rot = int(float(v.get("tags", {}).get("rotate", 0) or 0))
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd: rot = int(float(sd["rotation"]))
    if abs(rot) % 180 == 90: w, h = h, w
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    fps = 30.0 if not 10 <= fps <= 120 else (fps if fps <= 60 else fps / 2)
    dur = float(j["format"].get("duration") or v.get("duration") or 0)
    return dict(width=w, height=h, fps=fps, duration=dur, has_audio=any(s["codec_type"] == "audio" for s in j["streams"]))

def mean_brightness(path, duration, n=6):
    """Mean luma (0-1) over n evenly spaced small frames."""
    vals = []
    for k in range(n):
        t = duration * (k + 0.5) / n
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-vf", "scale=64:-2,format=gray",
                            "-f", "rawvideo", "-"], capture_output=True)
        if r.stdout: vals.append(np.frombuffer(r.stdout, np.uint8).mean() / 255)
    return float(np.mean(vals)) if vals else None

# ---------- the edit decision list ----------
def plan_cuts(words, duration, max_gap=0.55, pad=0.12, head=0.2, tail=0.5):
    """Source stretches to keep: speech with a little air around it; pauses longer than max_gap are shortened."""
    if not words: return [(0.0, duration)]
    segs, start = [], max(0.0, words[0]["start"] - head)
    for a, b in zip(words, words[1:]):
        if b["start"] - a["end"] > max_gap:
            segs.append((start, a["end"] + pad)); start = b["start"] - pad
    segs.append((start, min(duration, words[-1]["end"] + tail)))
    return [(round(a, 3), round(b, 3)) for a, b in segs if b - a > 0.05]

def remap(words, segs):
    """Word times on the edited timeline (words sit inside kept segments by construction)."""
    offs, acc = [], 0.0
    for a, b in segs:
        offs.append(acc); acc += b - a
    out = []
    for w in words:
        i = max(0, bisect.bisect_right([a for a, _ in segs], w["start"] + 1e-6) - 1)
        a, b = segs[i]
        d = offs[i] - a
        out.append(dict(w, start=round(w["start"] + d, 3), end=round(min(w["end"], b) + d, 3)))
    return out, acc

def shot_starts(words, segs, min_shot=2.2):
    """Edited-timeline moments where the framing may change: each cut, and each sentence start after min_shot."""
    cuts, acc = [], 0.0
    for a, b in segs[:-1]:
        acc += b - a; cuts.append(acc)
    starts = [0.0] + cuts
    for prev, w in zip(words, words[1:]):
        if prev["word"][-1] in ".!?" and w["start"] - starts[-1] >= min_shot:
            starts.append(w["start"])
    return sorted(set(round(s, 3) for s in starts))

def zoom_at(t, starts, words, edit):
    """Scale factor at edited time t: alternate per shot, slow push inside the shot, decaying punch on stressed words."""
    i = max(0, bisect.bisect_right(starts, t) - 1)
    z = 1.0 + edit["cut"] * (i % 2)
    end = starts[i + 1] if i + 1 < len(starts) else starts[i] + 6.0
    z += edit["push"] * min(1.0, max(0.0, (t - starts[i]) / max(0.5, end - starts[i])))
    if edit["punch"]:
        for w in words:
            dt = t - w["start"]
            if w.get("emph") and 0 <= dt < 1.2:
                z += edit["punch"] * min(1.0, dt / 0.05) * math.exp(-max(0.0, dt - 0.05) / 0.25)
    return z

# ---------- sound ----------
def build_audio(src, info, segs, out, music=None, music_db=-22.0, total=None):
    """Voice from the kept segments (clicks smoothed), optional music ducked under it, loudness to -14 LUFS."""
    if not info["has_audio"] and not music: return None
    ins, parts = ["-i", src], []
    if info["has_audio"]:
        for k, (a, b) in enumerate(segs):
            d = min(0.01, (b - a) / 4)
            parts.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,afade=t=in:d={d},afade=t=out:st={b - a - d:.3f}:d={d}[s{k}]")
        parts.append("".join(f"[s{k}]" for k in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[voice]")
    if music:
        ins += ["-stream_loop", "-1", "-i", music]
        parts.append(f"[1:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={music_db}dB,afade=t=out:st={max(0, total - 0.8):.3f}:d=0.8[mus]")
        if info["has_audio"]:
            parts.append("[voice]asplit=2[v1][v2]")
            parts.append("[mus][v1]sidechaincompress=threshold=0.03:ratio=10:attack=15:release=400[duck]")
            parts.append("[v2][duck]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            parts.append("[mus]anull[mix]")
    else:
        parts.append("[voice]anull[mix]")
    parts.append("[mix]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    subprocess.run(["ffmpeg", "-y", "-v", "error"] + ins + ["-filter_complex", ";".join(parts), "-map", "[aout]",
                   "-c:a", "aac", "-b:a", "192k", out], check=True)
    return out

# ---------- main render ----------
def target_shape(info, aspect):
    if aspect == "auto": aspect = "9x16" if info["height"] >= info["width"] else "16x9"
    return aspect, ((1080, 1920) if aspect == "9x16" else (1920, 1080))

def burn(src, out, words=None, text=None, style="auto", edit="subtle", cut_silence=False, aspect="auto",
         emphasis=True, music=None, music_db=-22.0, fx_every=None, progress=False, cover=None, srt=None,
         backend="faster", model=None, language=None, log=print):
    """Caption and edit src -> out. Returns dict(words, style, reasons, aspect, duration, cover, srt)."""
    info = probe(src)
    name = os.path.splitext(os.path.basename(out))[0]
    aspect, (W, H) = target_shape(info, aspect)
    if words is None:
        log("transcribing...")
        words = transcribe.transcribe(src, backend, model, language, prompt=markup.plain(text) if text else None)
        if text:
            r = transcribe.match_ratio(words, text)
            log(f"audio matches the passage {r:.0%}" + ("  (low: wrong recording?)" if r < 0.6 else ""))
            words = transcribe.snap_to_text(words, text)
    if not words: raise SystemExit("no speech found to caption")
    words = transcribe.as_plain(words) if "line" not in words[0] else words
    if emphasis: words = adapt.emphasize(words)

    segs = plan_cuts(words, info["duration"]) if cut_silence else [(0.0, info["duration"])]
    words, total = remap(words, segs)
    if cut_silence: log(f"cut silence: {info['duration']:.1f}s -> {total:.1f}s in {len(segs)} pieces")

    reasons = []
    if style == "auto":
        style, reasons = adapt.pick_style(words, name, aspect, mean_brightness(src, info["duration"]))
        log(f"style {style}: " + "; ".join(reasons))
    render.W, render.H, render.CONTRAST = W, H, True
    m = render.SAFE_9x16 if aspect == "9x16" else dict(left=0, top=0, right=0, bottom=0)
    render.SAFE = (m["left"], m["top"], W - m["right"], H - m["bottom"])
    render.STYLE = render.style_for(aspect, style, name, fx_every)
    phrases = render.group(words, render.STYLE)

    ed = EDITS[edit]
    starts = shot_starts(words, segs) if ed["cut"] or ed["push"] else [0.0]
    fps = info["fps"]
    DW, DH = even(W * HEADROOM), even(H * HEADROOM)
    vparts = [f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[c{k}]" for k, (a, b) in enumerate(segs)]
    vparts.append("".join(f"[c{k}]" for k in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[cat]")
    vparts.append(f"[cat]fps={fps},scale={DW}:{DH}:force_original_aspect_ratio=increase,crop={DW}:{DH},setsar=1,format=rgb24[vout]")
    work = tempfile.mkdtemp(prefix="burn_")
    silent, aud = os.path.join(work, "video.mp4"), os.path.join(work, "audio.m4a")
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-filter_complex", ";".join(vparts), "-map", "[vout]",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
                            "-i", "-", "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", silent], stdin=subprocess.PIPE)
    # the cover is the frame at the first stressed phrase (or 1 s in), captions included
    hot = next((p for p in phrases if p["solo"]), None)
    cover_i = int(((hot["start"] + 0.25) if hot else min(1.0, total / 2)) * fps)
    nbytes, cache, i = DW * DH * 3, {}, 0
    while True:
        buf = dec.stdout.read(nbytes)
        if len(buf) < nbytes: break
        t = i / fps
        z = zoom_at(t, starts, words, ed)
        cw, ch = DW / HEADROOM / z, DH / HEADROOM / z
        box = ((DW - cw) / 2, (DH - ch) / 2, (DW + cw) / 2, (DH + ch) / 2)
        big = Image.frombuffer("RGB", (DW, DH), buf, "raw", "RGB", 0, 1)
        bg = big.resize((W, H), Image.BICUBIC, box=box)
        frame = bg.convert("RGBA")
        if phrases:
            frame = Image.alpha_composite(frame, render.caption_layer(t, phrases, cache, (0, 0, W, H), bg=bg))
        rgb = frame.convert("RGB")
        if progress:
            d = ImageDraw.Draw(rgb, "RGBA"); bh, y = max(4, H // 220), int(H * 0.045)
            d.rectangle((0, y, W, y + bh), fill=(0, 0, 0, 90)); d.rectangle((0, y, int(W * t / total), y + bh), fill=(255, 255, 255, 235))
        if cover and i == cover_i: rgb.save(cover, quality=93)
        enc.stdin.write(rgb.tobytes()); i += 1
    enc.stdin.close(); dec.stdout.close()
    if enc.wait() or dec.wait(): raise SystemExit("ffmpeg failed while rendering")
    if cover and not os.path.exists(cover):                   # clip shorter than the chosen moment
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", silent, "-frames:v", "1", cover], check=True)

    a = build_audio(src, info, segs, aud, music, music_db, total)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", silent] + (["-i", a] if a else []) + ["-map", "0:v"] + (["-map", "1:a"] if a else []) \
          + ["-c", "copy", "-movflags", "+faststart"] + (["-shortest"] if a else []) + [out]
    subprocess.run(cmd, check=True)
    if srt:
        with open(srt, "w", encoding="utf-8") as f:
            f.write(transcribe.to_vtt(words) if srt.endswith(".vtt") else transcribe.to_srt(words))
    for f in (silent, aud):
        if os.path.exists(f): os.remove(f)
    os.rmdir(work)
    log(f"{out}: {total:.1f}s {W}x{H} {fps:g}fps, style {style}, edit {edit}")
    return dict(words=words, style=style, reasons=reasons, aspect=aspect, duration=total, cover=cover, srt=srt)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video"); ap.add_argument("out")
    ap.add_argument("--words", help="words.json to use instead of transcribing")
    ap.add_argument("--text", help="the passage (markup allowed) to snap the transcript to")
    ap.add_argument("--style", default="auto", choices=["auto", "random"] + sorted(styles.STYLES))
    ap.add_argument("--edit", default="subtle", choices=sorted(EDITS))
    ap.add_argument("--cut-silence", action="store_true", help="trim pauses longer than about half a second")
    ap.add_argument("--aspect", default="auto", choices=["auto", "9x16", "16x9"])
    ap.add_argument("--no-emphasis", action="store_true", help="don't stress words automatically")
    ap.add_argument("--fx-every", type=float, help="seconds between layout effects (0 = none)")
    ap.add_argument("--music"); ap.add_argument("--music-db", type=float, default=-22.0)
    ap.add_argument("--progress", action="store_true", help="thin progress bar near the top")
    ap.add_argument("--cover", help="save a cover image here"); ap.add_argument("--srt", help="write subtitles (.srt or .vtt)")
    ap.add_argument("--backend", default="faster", choices=sorted(transcribe.BACKENDS))
    ap.add_argument("--model"); ap.add_argument("--language")
    a = ap.parse_args()
    words = json.load(open(a.words)) if a.words else None
    text = open(a.text, encoding="utf-8").read() if a.text else None
    burn(a.video, a.out, words, text, a.style, a.edit, a.cut_silence, a.aspect, not a.no_emphasis, a.music, a.music_db,
         a.fx_every, a.progress, a.cover, a.srt, a.backend, a.model, a.language)

if __name__ == "__main__":
    main()
