"""Pick of the day: a short vertical video with the time lapse behind and the day's pick in front.

    python -m picks.render PICK.json SCREENSHOT.png FRAMES OUT.mp4 [--music track.mp3 --music-start 0:12]

FRAMES is a text file listing frame paths in order (as captions/render.py takes), or a folder of
TLS_*.jpg frames. The whole list plays across the clip, so pick the span you want behind the pick.
From Python (serving_quant):

    from picks import render_pick_video
    render_pick_video(pick_dict, "position.png", frame_paths, "out/pick_2026-10-15.mp4")

What is on screen, in order (default 15 s):
  0.3 s  PICK OF THE DAY, the date, then the market title
  1.8 s  the position screenshot rises in, large
  6.5 s  it shrinks up; the side and price, model vs market vs edge, and up to 3 forecast drivers come in
  -2.6 s  everything clears to the handle and the disclaimer
No voice. Music, when given, is trimmed to the clip with a short fade in and a longer fade out.
Next to OUT it also writes OUT_cover.jpg (the stats moment, for the post thumbnail) and OUT.txt
(plain text for the post's description).

The background reuses captions/render.py: same frame reading, deflicker, frame blending and camera moves.
"""
import argparse, glob, importlib.util, os, subprocess, sys
from functools import lru_cache
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .pick import Pick

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTIONS = os.path.join(REPO, "captions")


def _load_captions():
    """captions/render.py as a module (it imports markup and styles from its own folder)."""
    if CAPTIONS not in sys.path: sys.path.insert(0, CAPTIONS)
    spec = importlib.util.spec_from_file_location("captions_render", os.path.join(CAPTIONS, "render.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

cap = _load_captions()
import styles  # noqa: E402  (captions/styles.py, on the path from _load_captions)

FPS = 30
W, H = 1080, 1920
SAFE = (cap.SAFE_9x16["left"], cap.SAFE_9x16["top"], W - cap.SAFE_9x16["right"], H - cap.SAFE_9x16["bottom"])
CX = (SAFE[0] + SAFE[2]) // 2            # horizontal centre of the area the apps leave clear

WHITE, MUTED, ACCENT = (255, 255, 255), (214, 220, 228), (255, 190, 92)
SIDE_COLOR = {"YES": (34, 180, 110), "NO": (226, 76, 76)}
UP, DOWN = (110, 231, 160), (255, 130, 130)


@lru_cache(None)
def font(role, size):
    return ImageFont.truetype(styles.font(role), int(size))


def ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


# ---------- elements: drawn once, faded and slid in per frame ----------
def shadowed(layer, blur=10, strength=0.8, offset=(0, 4)):
    """layer with a soft dark shadow under it, padded so the shadow is not clipped."""
    pad = blur * 3
    out = Image.new("RGBA", (layer.width + 2 * pad, layer.height + 2 * pad))
    a = layer.getchannel("A").point(lambda v: int(v * strength))
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0)); sh.putalpha(a)
    out.alpha_composite(sh, (pad + offset[0], pad + offset[1]))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(layer, (pad, pad))
    return out, pad


def wrap(text, f, max_w):
    words, lines = text.split(), []
    for w in words:
        if lines and f.getlength(lines[-1] + " " + w) <= max_w: lines[-1] += " " + w
        else: lines.append(w)
    return lines


def text_el(text, role, size, color, max_w=None, spacing=1.15, tracking=0):
    f = font(role, size)
    lines = wrap(text, f, max_w) if max_w else [text]
    while max_w and len(lines) > 2 and size > 40:      # keep titles to two lines
        size *= 0.9; f = font(role, size); lines = wrap(text, f, max_w)
    lh = int(size * spacing)
    width = max(int(f.getlength(l) + tracking * len(l)) for l in lines) + 4
    im = Image.new("RGBA", (width, lh * len(lines) + int(size * 0.3)))
    d = ImageDraw.Draw(im)
    for i, l in enumerate(lines):
        if tracking:
            x = 0
            for ch in l:
                d.text((x, i * lh), ch, font=f, fill=color); x += f.getlength(ch) + tracking
        else:
            d.text((0, i * lh), l, font=f, fill=color)
    return im


class El:
    """An image placed at (x, y) (top-left, before its shadow pad), visible from t_in, rising `rise` px as it fades in."""
    def __init__(self, img, x, y, t_in, t_out=None, rise=24, fade=0.5, shadow=True, center=False):
        self.img, self.pad = shadowed(img) if shadow else (img, 0)
        self.x = int(x - img.width / 2) if center else int(x)
        self.y, self.t_in, self.t_out, self.rise, self.fade = int(y), t_in, t_out, rise, fade

    def alpha(self, t):
        a = ease((t - self.t_in) / self.fade)
        if self.t_out is not None: a *= 1 - ease((t - self.t_out) / 0.4)
        return a

    def draw(self, frame, t):
        a = self.alpha(t)
        if a <= 0.01: return
        img = self.img
        if a < 0.99:
            img = img.copy(); img.putalpha(img.getchannel("A").point(lambda v: int(v * a)))
        dy = int(self.rise * (1 - ease((t - self.t_in) / self.fade)))
        frame.alpha_composite(img, (self.x - self.pad, self.y - self.pad + dy))


def card(screenshot, radius=34):
    """The screenshot with rounded corners and a deep shadow, at full resolution."""
    sc = Image.open(screenshot).convert("RGBA")
    r = max(8, int(radius * sc.width / 760))
    m = Image.new("L", sc.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, sc.width - 1, sc.height - 1), r, fill=255)
    sc.putalpha(m)
    return sc


def frosted(frame, bg, box, a, radius=36):
    """Blurred, darkened background inside box: a glass panel the stats sit on."""
    if a <= 0.01: return
    x0, y0, x1, y1 = box
    reg = bg.crop(box)
    small = reg.resize((max(1, reg.width // 6), max(1, reg.height // 6)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3))
    glass = small.resize(reg.size, Image.BICUBIC).point(lambda v: int(v * 0.42)).convert("RGBA")
    m = Image.new("L", reg.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, reg.width - 1, reg.height - 1), radius, fill=int(255 * a))
    glass.putalpha(m)
    frame.alpha_composite(glass, (x0, y0))


def scrim():
    """A dark wash over the top two thirds so white text reads on a bright sky."""
    y = np.linspace(0, 1, H)[:, None]
    a = np.clip(0.4 - 0.28 * np.clip((y - 0.62) / 0.3, 0, 1), 0, 1) * 255
    im = np.zeros((H, W, 4), np.uint8); im[..., 3] = np.repeat(a, W, 1).astype(np.uint8)
    return Image.fromarray(im, "RGBA")


# ---------- layout ----------
def build(p, shot, seconds):
    L, T, R, B = SAFE
    tw = R - L
    t_stats, t_end = 6.5, seconds - 2.6
    els = []
    head = text_el("PICK OF THE DAY", "heavy", 40, ACCENT, tracking=5)
    els.append(El(head, L, T, 0.3, t_end))
    sub = " · ".join(x for x in (p.date, p.exchange) if x)
    if sub: els.append(El(text_el(sub, "light", 36, MUTED), L, T + 58, 0.5, t_end))
    title = text_el(p.market + (f" · {p.event}" if p.event else ""), "heavy", 70, WHITE, max_w=tw)
    els.append(El(title, L, T + 118, 0.9, t_end, rise=36))
    y = T + 118 + title.height + 30

    # screenshot: big, then shrinks to make room (rect = x0, y0, width)
    big_w = min(tw + 40, int((B - y - 10) * shot.width / shot.height))
    small_w = int(big_w * 0.5)
    sh_big = (CX - big_w // 2, y, big_w)
    sh_small = (CX - small_w // 2, y, small_w)
    y2 = y + int(small_w * shot.height / shot.width) + 30

    col = SIDE_COLOR.get(p.side, ACCENT)
    f = 64
    while font("heavy", f).getlength(p.headline()) > tw - 70 and f > 36: f -= 2
    txt = text_el(p.headline(), "heavy", f, WHITE)
    pill = Image.new("RGBA", (txt.width + 56, txt.height + 26))
    ImageDraw.Draw(pill).rounded_rectangle((0, 0, pill.width - 1, pill.height - 1), pill.height // 2, fill=col + (255,))
    pill.alpha_composite(txt, (28, 16))
    els.append(El(pill, CX, y2, t_stats + 0.4, t_end, center=True))
    y3 = y2 + pill.height + 30

    stats = []
    if p.model_prob is not None:
        e = p.edge_pts
        stats = [("MODEL", f"{p.model_prob * 100:.0f}%", WHITE), ("MARKET", f"{p.price_cents:g}%", WHITE),
                 ("EDGE", f"{e:+d}", UP if e > 0 else DOWN)]
    if stats:
        colw = tw // 3
        for i, (k, v, c) in enumerate(stats):
            x = L + colw * i + colw // 2
            els.append(El(text_el(k, "bold", 28, MUTED, tracking=3), x, y3, t_stats + 0.8 + 0.15 * i, t_end, center=True))
            els.append(El(text_el(v, "heavy", 64, c), x, y3 + 36, t_stats + 0.8 + 0.15 * i, t_end, center=True))
        y3 += 134
    for i, d in enumerate(p.drivers[:3]):
        el = text_el("•  " + d, "bold", 34, WHITE, max_w=tw - 40)
        if y3 + el.height > B + 40: break
        els.append(El(el, L + 20, y3, t_stats + 1.5 + 0.35 * i, t_end, rise=16))
        y3 += el.height + 6
    panel = (L - 24, y2 - 22, R + 24, min(H - 40, y3 + 18))

    # end: handle and disclaimer, centred
    end = []
    if p.handle: end.append(El(text_el(p.handle, "heavy", 76, WHITE), CX, H * 0.40, t_end + 0.4, center=True))
    end.append(El(text_el("PICK OF THE DAY", "heavy", 34, ACCENT, tracking=5), CX, H * 0.40 - 60, t_end + 0.3, center=True))
    if p.disclaimer: end.append(El(text_el(p.disclaimer, "bold", 36, MUTED), CX, H * 0.40 + 110, t_end + 0.6, center=True))
    return dict(els=els + end, sh_big=sh_big, sh_small=sh_small, panel=panel, t_stats=t_stats, t_end=t_end)


def lerp(a, b, k): return tuple(int(round(x + (y - x) * k)) for x, y in zip(a, b))


def draw_screenshot(frame, shot, lay, t):
    a = ease((t - 1.8) / 0.7) * (1 - ease((t - lay["t_end"]) / 0.4))
    if a <= 0.01: return
    k = ease((t - lay["t_stats"]) / 0.7)
    x, y, w = lerp(lay["sh_big"], lay["sh_small"], k)
    y += int(80 * (1 - ease((t - 1.8) / 0.7)))           # rises in
    h = int(w * shot.height / shot.width)
    im = shot.resize((w, h), Image.LANCZOS)
    im, pad = shadowed(im, blur=22, strength=0.7, offset=(0, 18))
    if a < 0.99: im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
    frame.alpha_composite(im, (x - pad, y - pad))


# ---------- frames ----------
def frame_list(frames):
    if isinstance(frames, (list, tuple)): return [str(f) for f in frames]
    if os.path.isdir(frames): return sorted(glob.glob(os.path.join(frames, "TLS_*.jpg")))
    return [l.strip() for l in open(frames) if l.strip()]


def render_pick_video(pick, screenshot, frames, out, music=None, music_start=0.0, music_db=0.0,
                      seconds=15.0, move="push", crf=16, deflicker=True):
    """Render the pick video to `out` (mp4). Returns the paths written: video, cover, post text."""
    p = Pick.load(pick)
    paths = frame_list(frames)
    if not paths: raise SystemExit("no frames")
    shot = card(screenshot)
    lay = build(p, shot, seconds)
    sc = scrim()
    n_out = int(round(seconds * FPS))
    fr = cap.Frames(paths, deflicker=deflicker)
    src_h, src_w = fr.get(0).shape[:2]
    mv = cap.MOVES[move]
    speed = (len(paths) - 1) / max(1, n_out - 1)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    silent = out.replace(".mp4", ".video.mp4") if music else out
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
                           "-pix_fmt", "yuv420p", "-movflags", "+faststart", silent], stdin=subprocess.PIPE)
    cover_at = int((lay["t_stats"] + 2.8) * FPS)
    cover = out.replace(".mp4", "_cover.jpg")
    for i in range(n_out):
        t = i / FPS
        pos = i * speed
        img = fr.window(pos, max(1.0, speed))
        box = cap.crop_box(src_w, src_h, i / max(1, n_out - 1), mv)
        bg = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC, box=box)
        frame = bg.convert("RGBA")
        k = ease(t / 0.8) * (1 - 0.4 * ease((t - lay["t_end"]) / 0.6))
        s = sc.copy(); s.putalpha(sc.getchannel("A").point(lambda v: int(v * k)))
        frame.alpha_composite(s)
        frosted(frame, bg, lay["panel"], ease((t - lay["t_stats"] - 0.2) / 0.5) * (1 - ease((t - lay["t_end"]) / 0.4)))
        draw_screenshot(frame, shot, lay, t)
        for el in lay["els"]: el.draw(frame, t)
        rgb = frame.convert("RGB")
        if i == cover_at: rgb.save(cover, quality=93)
        ff.stdin.write(rgb.tobytes())
    ff.stdin.close(); ff.wait()
    if ff.returncode: raise SystemExit("ffmpeg failed")
    if music:
        fade_out = max(0.0, seconds - 1.8)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", silent, "-ss", str(music_start), "-i", music,
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                        "-af", f"volume={music_db}dB,afade=t=in:d=0.4,afade=t=out:st={fade_out}:d=1.8",
                        "-t", str(seconds), "-movflags", "+faststart", out], check=True)
        os.remove(silent)
    txt = out.replace(".mp4", ".txt")
    open(txt, "w", encoding="utf-8").write(p.post_caption() + "\n")
    return out, cover, txt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pick", help="pick JSON (see picks/pick.py)")
    ap.add_argument("screenshot", help="position screenshot (png/jpg)")
    ap.add_argument("frames", help="text file of frame paths, or a folder of TLS_*.jpg")
    ap.add_argument("out")
    ap.add_argument("--music"); ap.add_argument("--music-start", default="0", help="seconds or m:ss into the track")
    ap.add_argument("--music-db", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--move", choices=sorted(cap.MOVES), default="push")
    ap.add_argument("--crf", type=int, default=16, help="x264 quality (lower is better; 16 is visually lossless)")
    ap.add_argument("--no-deflicker", action="store_true")
    a = ap.parse_args()
    for f in render_pick_video(a.pick, a.screenshot, a.frames, a.out, a.music, cap.parse_time(a.music_start),
                               a.music_db, a.seconds, a.move, a.crf, not a.no_deflicker):
        print(f)


if __name__ == "__main__":
    main()
