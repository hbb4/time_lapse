"""Pick of the day: a short vertical video with the time lapse behind and the day's pick in front.

    python -m picks.render PICK.json SCREENSHOT.png FRAMES OUT.mp4 [--music track.mp3 --music-start 0:12]

FRAMES is a text file listing frame paths in order (as captions/render.py takes), or a folder of
TLS_*.jpg frames. The whole list plays across the clip, so pick the span you want behind the pick.
From Python (serving_quant):

    from picks import render_pick_video
    render_pick_video(pick_dict, "position.png", frame_paths, "out/pick_2026-10-15.mp4")

What is on screen, in order (default 15 s), all in the lower part of the frame so the sky stays clear:
  0.1 s  PICK OF THE DAY with the picks' record on a gold badge, the date, the market title
  0.4 s  the position screenshot pops in
  3.2 s  it gives way to the pick: side and price, model vs market vs edge, and up to 2 forecast drivers
  -2.6 s  everything clears to the record (large), the handle and the disclaimer
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
    """An image placed at (x, y) (top-left, before its shadow pad), visible from t_in, rising `rise` px as it fades in.
    pop: it also scales up from 80% with a small overshoot. shift(t): extra vertical offset (glides with the box)."""
    def __init__(self, img, x, y, t_in, t_out=None, rise=24, fade=0.35, shadow=True, center=False, pop=False, shift=None):
        self.img, self.pad = shadowed(img) if shadow else (img, 0)
        self.x = int(x - img.width / 2) if center else int(x)
        self.y, self.t_in, self.t_out, self.rise, self.fade = int(y), t_in, t_out, rise, fade
        self.pop, self.shift = pop, shift

    def alpha(self, t):
        a = ease((t - self.t_in) / self.fade)
        if self.t_out is not None: a *= 1 - ease((t - self.t_out) / 0.4)
        return a

    def draw(self, frame, t):
        a = self.alpha(t)
        if a <= 0.01: return
        img, x, y = self.img, self.x - self.pad, self.y - self.pad
        k = min(1.0, max(0.0, (t - self.t_in) / self.fade))
        if self.pop and k < 1:
            z = 0.8 + 0.2 * cap.ease_back(k)
            w, h = max(1, int(img.width * z)), max(1, int(img.height * z))
            x, y = x + (img.width - w) // 2, y + (img.height - h) // 2
            img = img.resize((w, h), Image.BICUBIC)
        elif not self.pop:
            y += int(self.rise * (1 - ease(k)))
        if a < 0.99:
            img = img.copy(); img.putalpha(img.getchannel("A").point(lambda v: int(v * a)))
        if self.shift: y += int(self.shift(t))
        frame.alpha_composite(img, (x, y))


def badge(text, size, fill, ink):
    t = text_el(text, "heavy", size, ink)
    b = Image.new("RGBA", (t.width + int(size * 1.1), t.height + int(size * 0.45)))
    ImageDraw.Draw(b).rounded_rectangle((0, 0, b.width - 1, b.height - 1), b.height // 2, fill=fill + (255,))
    b.alpha_composite(t, (int(size * 0.55), int(size * 0.3)))
    return b


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
    """A dark wash behind the text in the lower part of the frame; the sky above is left as it is."""
    y = np.linspace(0, 1, H)[:, None]
    a = 0.42 * np.clip((y - 0.28) / 0.25, 0, 1) * 255
    im = np.zeros((H, W, 4), np.uint8); im[..., 3] = np.repeat(a, W, 1).astype(np.uint8)
    return Image.fromarray(im, "RGBA")


# ---------- layout ----------
SHOT_W = 520          # screenshot width on screen: small enough to leave the sky clear
INK = (34, 24, 8)     # dark text on the gold record badge


def build(p, shot, seconds, max_drivers=2):
    """Everything sits in the lower part of the frame, bottom-aligned just above the apps' caption area:
    header and record, title, then the screenshot. At t_stats the screenshot gives way to the shorter pick
    panel and the header and title glide down to sit on top of it."""
    L, T, R, B = SAFE
    tw, bot = R - L, B + 30
    t_stats, t_end = 3.2, seconds - 2.6

    # the pick panel, measured first so the header can glide onto it
    col = SIDE_COLOR.get(p.side, ACCENT)
    f = 58
    while font("heavy", f).getlength(p.headline()) > tw - 70 and f > 34: f -= 2
    pill = badge(p.headline(), f, col, WHITE)
    stats = []
    if p.model_prob is not None:
        e = p.edge_pts
        stats = [("MODEL", f"{p.model_prob * 100:.0f}%", WHITE), ("MARKET", f"{p.price_cents:g}%", WHITE),
                 ("EDGE", f"{e:+d}", UP if e > 0 else DOWN)]
    drivers = [text_el("•  " + d, "bold", 32, WHITE, max_w=tw - 40) for d in p.drivers[:max_drivers]]
    panel_h = 22 + pill.height + (26 + 128 if stats else 0) + sum(d.height + 4 for d in drivers) + 18
    panel_top = bot - panel_h

    shot_h = int(SHOT_W * shot.height / shot.width)
    shot_top = bot - shot_h
    glide = panel_top - shot_top
    move = lambda t: glide * ease((t - t_stats) / 0.6)

    title = text_el(p.market + (f" · {p.event}" if p.event else ""), "heavy", 58, WHITE, max_w=tw)
    title_y = shot_top - 26 - title.height
    head = text_el("PICK OF THE DAY", "heavy", 34, ACCENT, tracking=5)
    rec = badge(p.record_text(), 32, ACCENT, INK) if p.record_text() else None
    head_h = rec.height if rec else head.height
    sub = " · ".join(x for x in (p.date, p.exchange) if x)
    sub_el = text_el(sub, "light", 32, MUTED) if sub else None
    sub_y = title_y - 6 - (sub_el.height if sub_el else 0)
    head_y = sub_y - 8 - head_h

    els = [El(head, L, head_y + (head_h - head.height) // 2 + 4, 0.1, t_end, shift=move)]
    if rec:
        rx = L + head.width + 24 if L + head.width + 24 + rec.width <= R else L
        els.append(El(rec, rx, head_y if rx > L else head_y - rec.height - 8, 0.25, t_end, pop=True, fade=0.4, shift=move))
    if sub_el: els.append(El(sub_el, L, sub_y, 0.15, t_end, shift=move))
    els.append(El(title, L, title_y, 0.2, t_end, rise=30, shift=move))

    y = panel_top + 22
    els.append(El(pill, CX, y, t_stats + 0.15, t_end, center=True, pop=True, fade=0.4))
    y += pill.height + 26
    if stats:
        colw = tw // 3
        for i, (k, v, c) in enumerate(stats):
            x = L + colw * i + colw // 2
            els.append(El(text_el(k, "bold", 28, MUTED, tracking=3), x, y, t_stats + 0.35 + 0.1 * i, t_end, center=True))
            els.append(El(text_el(v, "heavy", 62, c), x, y + 34, t_stats + 0.35 + 0.1 * i, t_end, center=True))
        y += 128
    for i, d in enumerate(drivers):
        els.append(El(d, L + 20, y, t_stats + 0.8 + 0.25 * i, t_end, rise=16))
        y += d.height + 4
    panel = (L - 24, panel_top, R + 24, bot)

    # end: the record big, then handle and disclaimer, centred
    end, ey = [], H * 0.36
    if p.record_text():
        end.append(El(text_el("PICKS RECORD", "heavy", 32, ACCENT, tracking=5), CX, ey, t_end + 0.25, center=True))
        big = text_el(p.record_text().split(" · ")[0], "heavy", 150, ACCENT)
        end.append(El(big, CX, ey + 44, t_end + 0.35, center=True, pop=True, fade=0.45))
        ey += 44 + big.height + 10
        if " · " in p.record_text():
            end.append(El(text_el(p.record_text().split(" · ")[1] + " WINS", "heavy", 44, WHITE, tracking=3), CX, ey,
                          t_end + 0.5, center=True))
            ey += 80
    if p.handle: end.append(El(text_el(p.handle, "heavy", 64, WHITE), CX, ey + 20, t_end + 0.6, center=True)); ey += 110
    if p.disclaimer: end.append(El(text_el(p.disclaimer, "bold", 32, MUTED), CX, ey + 10, t_end + 0.75, center=True))
    return dict(els=els + end, shot=(CX - SHOT_W // 2, shot_top, SHOT_W), panel=panel, t_stats=t_stats, t_end=t_end)


def draw_screenshot(frame, shot, lay, t):
    """Pops in at 0.4 s, then shrinks and fades as the pick panel takes its place."""
    t_in, t_out = 0.4, lay["t_stats"] - 0.1
    a = ease((t - t_in) / 0.35) * (1 - ease((t - t_out) / 0.3))
    if a <= 0.01: return
    k = min(1.0, max(0.0, (t - t_in) / 0.4))
    z = (0.82 + 0.18 * cap.ease_back(k)) * (1 - 0.1 * ease((t - t_out) / 0.3))
    x0, y0, w0 = lay["shot"]
    w = int(w0 * z); h = int(w * shot.height / shot.width)
    h0 = int(w0 * shot.height / shot.width)
    x, y = x0 + (w0 - w) // 2, y0 + (h0 - h) // 2
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
                      seconds=15.0, move="push", crf=16, deflicker=True, max_drivers=2):
    """Render the pick video to `out` (mp4). Returns the paths written: video, cover, post text."""
    p = Pick.load(pick)
    paths = frame_list(frames)
    if not paths: raise SystemExit("no frames")
    shot = card(screenshot)
    lay = build(p, shot, seconds, max_drivers)
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
    cover_at = int((lay["t_stats"] + 2.0) * FPS)
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
        frosted(frame, bg, lay["panel"], ease((t - lay["t_stats"]) / 0.3) * (1 - ease((t - lay["t_end"]) / 0.4)))
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
    ap.add_argument("--max-drivers", type=int, default=2, help="forecast driver lines shown (0 for none)")
    ap.add_argument("--no-deflicker", action="store_true")
    a = ap.parse_args()
    for f in render_pick_video(a.pick, a.screenshot, a.frames, a.out, a.music, cap.parse_time(a.music_start),
                               a.music_db, a.seconds, a.move, a.crf, not a.no_deflicker, a.max_drivers):
        print(f)


if __name__ == "__main__":
    main()
