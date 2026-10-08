"""Render a clip: time lapse frames + camera move + word-by-word captions synced to a reading + music.

The whole frame span plays across the reading, so any passage length gets the same stretch of the evening
(--fit realtime plays 1:1 instead). Each output frame is a weighted blend of the source frames it covers,
so motion stays smooth at any speed. Optional light trails, beat-synced zoom punches, *stressed* words
(shown alone on screen) and captions placed on surfaces in the scene (roof edge, walls) via passage markup
(see markup.py). Caption looks live in styles.py (--style).

Demo: frames 380-1650 of TLST05B01403_260921165611 (Sep 21 2026, 17:47-20:36, 8 s interval).
"""
import argparse, json, os, subprocess, wave
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
import markup, styles

FPS = 30
W, H = 1080, 1920                      # set by --aspect
STYLE = None                           # set from --style in main (styles.py); y=0.66 sits below the horizon line
ROTATE = Image.Transpose.ROTATE_270    # camera frames are sideways: 90 deg clockwise, as make_timelapse.sh "cw"
SAFE = None                            # (left, top, right, bottom) captions stay inside; set in main (--safe)
CONTRAST = True                        # captions get a stronger shadow over bright backgrounds (--contrast)

# Areas the apps cover with buttons and text on vertical video (TikTok, Reels, Shorts), in 1080x1920 pixels:
# the right edge (like/comment/share column), the bottom (caption, sound, account name) and the top bar.
SAFE_9x16 = dict(left=64, top=260, right=150, bottom=480)

# Surfaces in this view, as corners TL, TR, BR, BL in upright source pixels (1520x2688).
# Captions tagged {name} in the passage are drawn onto these in perspective and move with the camera.
PLANES = {
    "edge":  [(200, 2037), (600, 1897), (600, 2047), (200, 2187)],   # standing on the near roof's front edge
    "front": [(200, 2187), (600, 2047), (600, 2167), (200, 2300)],   # painted on the near roof's siding
    "side":  [(997, 1900), (1387, 1783), (1360, 1889), (1000, 2087)],  # right wall of the building behind
    "rooftop": [(860, 1785), (1230, 1765), (1330, 1905), (660, 1985)], # lying flat on the near roof
    "yellow":  [(410, 1425), (765, 1440), (765, 1565), (410, 1560)],   # face of the yellow building mid-frame
    "tan":     [(15, 1235), (380, 1245), (380, 1460), (15, 1475)],     # tall tan building on the left, over its windows
    "hills":   [(560, 1150), (1160, 1165), (1160, 1265), (560, 1250)], # small, along the far ridge behind the skyline
    "sky":     [(220, 560), (1300, 560), (1300, 860), (220, 860)],     # floating flat, high in the open sky
    "clouds":  [(-250, 300), (1770, 300), (1150, 760), (370, 760)],    # on the cloud ceiling, receding toward the horizon
}

# ---------- phrase grouping: decides how the words are displayed ----------
FUNC = {"a", "an", "the", "at", "of", "to", "in", "it", "and", "is", "on", "my", "your"}

def bare(w): return w["word"].strip(".,;:!?\"'").lower()

FX = ("stairs", "cascade", "stack", "spread")

def fx_ranges(words, s, auto=True):
    """Lines drawn with a layout effect: {start index: (end index, effect)}.
    Marked with [effect] in the passage, or picked automatically: candidate lines (a verse line, or in prose
    a sentence or clause) of 2-8 words with no stressed word or surface, scored (closing line, stanza end,
    ends in ! or ?, short and punchy), then spread out so there is about one every s["fx_every"] seconds
    (fewer for short clips, more for long ones; never two closer than half that). The closing line always
    gets one if it qualifies."""
    out, n = {}, len(words)
    if not n: return out
    def span_from(i):                       # to the end of the line, or the sentence if that comes first, max 12 words
        j = i
        while j + 1 < n and words[j + 1].get("line") == words[i].get("line") and j - i < 11 \
                and words[j]["word"][-1] not in ".!?;":
            j += 1
        return j
    for i, w in enumerate(words):
        if w.get("fx") and w["fx"] in FX:
            out[i] = (span_from(i), w["fx"])
    if not auto or not s.get("fx") or not s.get("fx_every"):
        return out
    # candidate units: verse lines, or sentences / clauses in prose
    lines = {}
    for k, w in enumerate(words): lines.setdefault(w.get("line", 0), []).append(k)
    verse = len(lines) >= 3 and n / len(lines) < 10
    units, cur = [], []
    for k, w in enumerate(words):
        cur.append(k)
        brk = w.get("line_end") if verse else (w["word"][-1] in ".!?;:" or w.get("line_end"))
        if brk or k == n - 1: units.append(cur); cur = []
    cands = []
    for u in units:
        i, j = u[0], u[-1]
        ws = words[i:j + 1]
        if not 2 <= len(ws) <= 8 or any(x.get("emph") or x.get("plane") or x.get("fx") for x in ws):
            continue
        if any(a <= j and i <= b for a, (b, _) in out.items()):
            continue
        last = ws[-1]["word"]
        score = (5 if j == n - 1 else 0) + (3 if words[j].get("stanza_end") else 0) + \
                (2 if last[-1] in "!?" else 0) + (1 if len(ws) <= 5 else 0) + (1 if last[-1] in ".!?" else 0)
        cands.append((score, i, j))
    dur = words[-1]["end"] - words[0]["start"]
    every = s["fx_every"]
    want = max(1, round(dur / every))
    taken, picked = [(i, j) for i, (j, _) in out.items()], 0
    for score, i, j in sorted(cands, key=lambda c: (-c[0], c[1])):
        if picked >= want: break
        t0 = words[i]["start"]
        if all(abs(t0 - words[a]["start"]) >= every * 0.5 for a, _ in taken):
            taken.append((i, j)); picked += 1
    cycle = 0
    for i, j in sorted(taken):
        if i not in out:
            out[i] = (j, s["fx"][cycle % len(s["fx"])]); cycle += 1
    return out

def group(words, s=None, auto_fx=True):
    s = s or STYLE
    fxr = fx_ranges(words, s, auto_fx)
    phrases, cur, skip_to = [], [], -1
    for i, w in enumerate(words):
        if i <= skip_to:
            continue
        if i in fxr:                             # an effect line is one phrase, drawn with its layout
            if cur: phrases.append(cur); cur = []
            j, fx = fxr[i]
            run = [dict(x, brk=True) for x in words[i:j + 1]]
            run[0]["fx_layout"] = fx
            phrases.append(run); skip_to = j
            continue
        prev = words[i - 1] if i else None
        stress_edge = prev is not None and bool(w.get("emph")) != bool(prev.get("emph"))
        if (w.get("brk") or stress_edge) and cur:   # markup: {plane}, | or a *stressed* run starts a new phrase
            phrases.append(cur); cur = []
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if w.get("emph"):                        # a stressed run stays together, alone on screen
            if not (nxt and nxt.get("emph")): phrases.append(cur); cur = []
            continue
        gap = nxt["start"] - w["end"] if nxt else 9
        chars = sum(len(x["word"]) + 1 for x in cur)
        dangling = bare(w) in FUNC               # never break right after "the", "a", "of"...
        if (w["word"][-1] in ".,;:!?" or (gap > 0.45 and not dangling) or len(cur) >= s.get("max_words", 5)
                or chars > s["max_chars"] * s["max_lines"] - 4 or not nxt):
            phrases.append(cur); cur = []
    # no orphans: a lone short word ("It", "a", "go.") joins its neighbour (unless markup placed it)
    i = 0
    while i < len(phrases):
        p = phrases[i]
        solo = lambda q: any(x.get("emph") for x in q)
        if len(p) == 1 and len(bare(p[0])) < 4 and len(phrases) > 1 and not p[0].get("brk") and not solo(p):
            if (p[0]["word"][-1] in ".!?" or i + 1 == len(phrases)) and i > 0 and not solo(phrases[i - 1]) \
                    and not phrases[i - 1][0].get("fx_layout"):
                phrases[i - 1] += p
            elif i + 1 < len(phrases) and not phrases[i + 1][0].get("brk") and not solo(phrases[i + 1]):
                phrases[i + 1] = p + phrases[i + 1]
            else:
                i += 1; continue
            del phrases[i]; continue
        i += 1
    for i, p in enumerate(phrases):
        nxt = phrases[i + 1][0]["start"] if i + 1 < len(phrases) else 1e9
        end = min(p[-1]["end"] + s["hold"], nxt - 0.02)
        # a short, weighty phrase (1-2 words, one long word) gets the big treatment
        big = len(p) <= 2 and max(len(bare(x)) for x in p) >= 6 and bare(p[-1]) not in FUNC
        solo = all(x.get("emph") for x in p)
        phrases[i] = dict(words=p, start=p[0]["start"], end=end, big=big and not p[0].get("fx_layout"), solo=solo,
                          plane=None if solo else p[0].get("plane"), fx=p[0].get("fx_layout"))
    return phrases

def shown(w, s, solo=False):
    case = s.get("stress_case", s["case"]) if solo else s["case"]
    return w["word"].upper() if case == "upper" else w["word"].lower() if case == "lower" else w["word"]

def layout(ph, draw, box, size, max_chars, y_center, s=None):
    """Fix each word's final position up front so words land in place and never shift.
    Returns [(word, center x, baseline y, font, text)]."""
    s = s or STYLE
    solo = ph.get("solo")
    path = s["stress_path"] if solo else s["font_path"]
    if solo: size, max_chars = size * s["stress_size"], 60
    def fnt(sz): return ImageFont.truetype(path, int(sz))
    if ph.get("fx") and not solo:
        return fx_layout(ph, draw, box, size, y_center, s, fnt)
    while True:
        lines, line = [], []
        for w in ph["words"]:
            if line and len(" ".join(x["word"] for x in line + [w])) > max_chars:
                lines.append(line); line = []
            line.append(w)
        lines.append(line)
        f = fnt(size)
        space, lh = draw.textlength(" ", font=f), size * 1.12
        widths = [[draw.textlength(shown(w, s, solo), font=f) for w in ln] for ln in lines]
        if max(sum(ws) + space * (len(ws) - 1) for ws in widths) <= box[0] * 0.92 or size < 20:
            break
        size *= 0.92                                     # shrink to fit (planes, 16:9)
    y0 = y_center - lh * len(lines) / 2
    placed = []
    for li, (ln, ws) in enumerate(zip(lines, widths)):
        x = (box[0] - sum(ws) - space * (len(ln) - 1)) / 2
        base = y0 + li * lh + size * 0.86
        for w, wd in zip(ln, ws):
            placed.append((w, x + wd / 2, base, f, shown(w, s, solo)))
            x += wd + space
    return placed

def chunk(words, n_lines):
    """Split words into n_lines lines of similar length, never ending a line on "the", "a", "of"..."""
    if n_lines <= 1: return [words]
    total = sum(len(w["word"]) + 1 for w in words)
    lines, cur, acc = [], [], 0
    for k, w in enumerate(words):
        cur.append(w); acc += len(w["word"]) + 1
        left = len(words) - k - 1
        if len(lines) < n_lines - 1 and acc >= total * (len(lines) + 1) / n_lines and left and bare(w) not in FUNC:
            lines.append(cur); cur = []
    if cur: lines.append(cur)
    return lines

def fx_layout(ph, draw, box, size, y_center, s, fnt):
    """Layout effects for a key line. Positions are fixed up front like normal captions; words still appear
    as they're spoken. stairs: each word (or pair) steps down and starts where the last one ended.
    cascade: short lines, each indented a step further. stack: 2-4 short lines, each sized to the same width.
    spread: one line with wide word spacing."""
    fx, words = ph["fx"], ph["words"]
    n = len(words)
    if fx == "stairs":
        lines = [words[k:k + (2 if n > 6 else 1)] for k in range(0, n, 2 if n > 6 else 1)]
    elif fx in ("cascade", "stack"):
        lines = chunk(words, min(4, max(2, round(n / 2.5))))
    else:
        lines = [words]
    W_, H_ = box
    for _ in range(40):
        f = fnt(size)
        space = draw.textlength(" ", font=f) * (3.2 if fx == "spread" else 1)
        def width(ln, g, sp):
            return sum(draw.textlength(shown(w, s), font=g) for w in ln) + sp * (len(ln) - 1)
        fonts, spaces = [f] * len(lines), [space] * len(lines)
        if fx == "stack":                      # every line scaled to the widest natural line
            nat = [width(ln, f, space) for ln in lines]
            target = max(nat)
            fonts = [fnt(min(size * 2.2, size * target / max(1, x))) for x in nat]
            spaces = [draw.textlength(" ", font=g) for g in fonts]
        wl = [width(ln, g, sp) for ln, g, sp in zip(lines, fonts, spaces)]
        hl = [g.size * (0.98 if fx == "stack" else 1.12) for g in fonts]
        if fx == "stairs":
            xs = [0.0]
            for x in wl[:-1]: xs.append(xs[-1] + x * 0.8)
        elif fx == "cascade":
            xs = [k * size * 1.4 for k in range(len(lines))]
        else:
            xs = [-x / 2 for x in wl]          # centered
        if fx in ("stairs", "cascade"):
            bw = max(x + w_ for x, w_ in zip(xs, wl))
        else:
            bw = max(wl)
        bh = sum(hl)
        if (bw <= W_ * 0.9 and bh <= H_ * 0.45) or size < 16:
            break
        size *= 0.93
    yc = min(max(y_center, bh / 2 + H_ * 0.06), H_ * 0.96 - bh / 2)   # keep the block inside the frame
    top = yc - bh / 2
    left = (W_ - bw) / 2 if fx in ("stairs", "cascade") else W_ / 2
    placed, y = [], top
    for ln, g, sp, x0, h in zip(lines, fonts, spaces, xs, hl):
        y += h
        x = left + x0
        for w in ln:
            t = shown(w, s); tw = draw.textlength(t, font=g)
            placed.append((w, x + tw / 2, y - h * 0.16, g, t)); x += tw + sp
    return placed

def ease_back(t):  # overshoot "pop"
    c = 1.9; t -= 1
    return 1 + (c + 1) * t ** 3 + c * t ** 2

def draw_words(size, placed, ph, t, shadow_blur, s=None, bright=0.0):
    """bright (0-1): how bright the background behind the words is; brighter = darker, wider shadow."""
    s = s or STYLE
    layer = Image.new("RGBA", size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    glow = Image.new("RGBA", size, (0, 0, 0, 0)); g = ImageDraw.Draw(glow)
    out = min(1, max(0, (t - ph["end"]) / s["fade"]))          # phrase exit progress
    u = size[1] / H
    solo, enter = ph.get("solo"), s["enter"]
    for w, cx, base, font, text in placed:
        if t < w["start"]:
            continue
        k = min(1, (t - w["start"]) / (0.12 if solo else s["pop"]))
        f, dy, alpha = font, 0.0, min(1, k * 2)
        if solo:                                                   # stressed: alone, still, no zoom
            alpha = k * k * (3 - 2 * k)
        elif enter == "pop":
            f = font.font_variant(size=max(8, int(font.size * (0.55 + 0.45 * ease_back(k)))))
        elif enter == "fade":
            alpha = k * k * (3 - 2 * k)
        elif enter == "rise":
            alpha, dy = k, 34 * u * (1 - k) ** 2
        elif enter == "slide":
            alpha, dy = min(1, k * 3), 70 * u * (1 - k) ** 3
        elif enter == "type":                                      # letters appear across the spoken word
            dur = max(0.08, min(0.45, w["end"] - w["start"]))
            text = text[:max(1, round(len(text) * min(1, (t - w["start"]) / dur)))]
            alpha = 1
        alpha = int(255 * alpha * (1 - out))
        active = w["start"] <= t < w["end"] + 0.08 and out == 0
        col = s["stress"] if solo else (s["active"] if active else s["color"])
        y = base + dy - 22 * out * u                               # drift up on exit
        if enter == "type" and not solo:                           # anchor left so letters don't shift
            x = cx - d.textlength(shown(w, s), font=f) / 2
            d.text((x, y), text, font=f, fill=col + (alpha,), anchor="ls")
        else:
            d.text((cx, y), text, font=f, fill=col + (alpha,), anchor="ms")
            if solo and s["glow"]:
                g.text((cx, y), text, font=f, fill=s["stress"] + (int(alpha * 0.8),), anchor="ms")
    base_img = Image.new("RGBA", size, (0, 0, 0, 0))
    a = layer.split()[3]
    b = bright
    strength = (s["shadow"] + (1 - s["shadow"]) * b) * (1 + 0.5 * b)
    sh = a.filter(ImageFilter.GaussianBlur(shadow_blur * (1 + 0.5 * b))).point(lambda v: min(255, int(v * strength)))
    if b > 0.05:                                  # bright sky or fog: a soft dark halo around the words
        halo = a.filter(ImageFilter.GaussianBlur(shadow_blur * 4)).point(lambda v: min(int(140 * b), int(v * 2.5 * b)))
        sh = ImageChops.lighter(sh, halo)
    base_img.putalpha(sh)
    if glow.getbbox():
        glow = glow.filter(ImageFilter.GaussianBlur(shadow_blur * 1.4))
        base_img = Image.alpha_composite(base_img, glow)
    return Image.alpha_composite(base_img, layer)

def homography(src_pts, dst_pts):
    """Coefficients for PIL's PERSPECTIVE transform: maps output (dst) points back to input (src) points."""
    A, b = [], []
    for (x, y), (u, v) in zip(dst_pts, src_pts):
        A += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        b += [u, v]
    return np.linalg.solve(np.array(A, float), np.array(b, float)).tolist()

def bounds(placed):
    """Bounding box (x0, y0, x1, y1) of laid-out words."""
    xs, ys = [], []
    for _, cx, base, f, text in placed:
        w = f.getlength(text)
        xs += [cx - w / 2, cx + w / 2]; ys += [base - f.size * 0.9, base + f.size * 0.25]
    return min(xs), min(ys), max(xs), max(ys)

def into_safe(placed):
    """Move laid-out words (laid out in a box the safe area's width) inside the safe area."""
    l, t, r, b = SAFE
    _, y0, _, y1 = bounds(placed)
    dy = max(t - y0, 0) + min(b - y1, 0) if y1 - y0 < b - t else t - y0
    return [(w, cx + l, base + dy, f, text) for w, cx, base, f, text in placed]

def brightness(bg, rect):
    """How bright the background is behind the words: 0 (dark enough for light text) to 1 (very bright)."""
    if bg is None or not CONTRAST: return 0.0
    x0, y0, x1, y1 = [int(v) for v in rect]
    x0, y0, x1, y1 = max(0, x0 - 20), max(0, y0 - 20), min(W, x1 + 20), min(H, y1 + 20)
    if x1 - x0 < 4 or y1 - y0 < 4: return 0.0
    patch = bg.crop((x0, y0, x1, y1)).convert("L").resize((64, max(4, int(64 * (y1 - y0) / (x1 - x0)))))
    lum = np.percentile(np.asarray(patch, np.float32), 75) / 255   # bright patches matter more than the average
    return float(np.clip((lum - 0.35) / 0.35, 0, 1))

def contrast_k(cache, ph, t, k):
    """Smooth k over about half a second so the shadow never flickers; a new phrase starts at its own value."""
    last = cache.get("_k")
    if not last or last[0] is not ph:
        cache["_k"] = (ph, t, k); return k
    _, t0, k0 = last
    k = k0 + (k - k0) * min(1, (t - t0) / 0.5)
    cache["_k"] = (ph, t, k)
    return k

def caption_layer(t, phrases, cache, box, s=None, bg=None):
    s = s or STYLE
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ph = next((p for p in phrases if p["start"] <= t < p["end"] + s["fade"]), None)
    if not ph:
        return layer
    u = min(W, H) / 1080                                   # styles are authored for 1080 wide
    plane = ph["plane"]
    if plane:                    # a surface outside the current framing (e.g. the near roof in 16:9) falls back to flat
        q = np.array(PLANES[plane], float)
        if q[:, 0].min() < box[0] or q[:, 0].max() > box[2] or q[:, 1].min() < box[1] or q[:, 1].max() > box[3]:
            plane = None
    if not plane:
        key = (id(ph), "flat")
        if key not in cache:
            size = (s["big"] if ph["big"] else s["size"]) * u
            l, _, r, _ = SAFE
            placed = into_safe(layout(ph, ImageDraw.Draw(layer), (r - l, H), size, s["max_chars"], H * s["y"]))
            cache[key] = (placed, bounds(placed))
        placed, rect = cache[key]
        k = contrast_k(cache, ph, t, brightness(bg, rect))
        return draw_words((W, H), placed, ph, t, 14 * u, bright=k)
    # on a surface: draw flat at 2x on a canvas shaped like the surface, then warp it into the shot
    q = np.array(PLANES[ph["plane"]], float)
    cw = int(2 * (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2)
    ch = int(2 * (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2)
    key = (id(ph), "plane")
    if key not in cache:
        cache[key] = layout(ph, ImageDraw.Draw(layer), (cw, ch), ch * 0.78, 40, ch * 0.5)
    sx = W / (box[2] - box[0])
    dst = [((x - box[0]) * sx, (y - box[1]) * sx) for x, y in q]
    d = np.array(dst)
    k = contrast_k(cache, ph, t, brightness(bg, (*d.min(0), *d.max(0))))
    flat = draw_words((cw, ch), cache[key], ph, t, 4, bright=k)
    coeffs = homography([(0, 0), (cw, 0), (cw, ch), (0, ch)], dst)
    return flat.transform((W, H), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)

def plane_look(t, phrases, src_w, src_h, zoom, ramp=0.6, s=None):
    s = s or STYLE
    """While a caption sits on a surface, the camera leans toward it and eases back after."""
    for p in phrases:
        if p["plane"] and p["start"] - ramp <= t <= p["end"] + s["fade"] + ramp:
            w = min(1, (t - p["start"] + ramp) / ramp, (p["end"] + s["fade"] + ramp - t) / ramp)
            c = np.array(PLANES[p["plane"]], float).mean(0)
            return (c[0] / src_w, c[1] / src_h, zoom, max(0, w))
    return None

# ---------- camera moves: crop box (x0, y0, x1, y1) in source pixels, t in 0..1 ----------
def smooth(t): return t * t * (3 - 2 * t)

MOVES = {   # zoom range and focus point (fraction of the upright frame); min zoom clears the lens vignette
    "push":   dict(zoom=(1.12, 1.28), focus=((0.5, 0.5), (0.52, 0.47))),    # slow push toward the skyline
    "pull":   dict(zoom=(1.30, 1.12), focus=((0.52, 0.47), (0.5, 0.5))),    # reveal
    "tilt":   dict(zoom=(1.18, 1.18), focus=((0.5, 0.62), (0.5, 0.40))),    # rooftops up to the sky
    "still":  dict(zoom=(1.14, 1.14), focus=((0.5, 0.5), (0.5, 0.5))),
    "drift":  dict(zoom=(1.3, 1.3), focus=((0.2, 0.5), (0.45, 0.5))),       # slow sideways glide, ends on the 16:9 framing
}

LONG_SHIFT_X = 0.3       # 16:9 framing: how far right of the move's focus (0 = as the move says)
LONG_MIN_ZOOM = 1.3      # 16:9 framing: closer than the vertical moves, so there is room to sit right
LONG_FOCUS_Y = 0.47      # 16:9 framing: vertical focus, held for every move except tilt (Blake's pick, before the raise)

def crop_box(src_w, src_h, t, move, punch=0.0, look=None):
    """look = (fx, fy, zoom, weight): ease the camera toward a point, e.g. a surface carrying a caption."""
    aspect = W / H
    usable_h = src_h * 0.94
    full_h = min(usable_h, src_w / aspect)
    z = move["zoom"][0] + (move["zoom"][1] - move["zoom"][0]) * smooth(t)
    fx = move["focus"][0][0] + (move["focus"][1][0] - move["focus"][0][0]) * smooth(t)
    fy = move["focus"][0][1] + (move["focus"][1][1] - move["focus"][0][1]) * smooth(t)
    if look and look[3] > 0:
        k = smooth(look[3])
        fx, fy, z = fx + (look[0] - fx) * k, fy + (look[1] - fy) * k, z + (look[2] - z) * k
    z *= 1 + punch
    if aspect > 1: z = max(z, LONG_MIN_ZOOM)
    ch = full_h / z; cw = ch * aspect
    if aspect > 1:                               # long form (16:9), per Blake:
        if move is not MOVES["tilt"]: fy = LONG_FOCUS_Y
        fy -= 0.25 * ch / src_h                  #   a quarter of the crop height higher (more sky)
        fx += LONG_SHIFT_X                       #   and toward the right (less big building, more sunset)
    x0 = min(max(fx * src_w - cw / 2, 0), src_w - cw)
    y0 = min(max(fy * src_h - ch / 2, 0), usable_h - ch)
    return (x0, y0, x0 + cw, y0 + ch)

SR = 44100

def parse_time(v):
    """'83.5', '1:23.5' or '0:01:23.5' -> seconds."""
    sec = 0.0
    for part in str(v).split(":"):
        sec = sec * 60 + float(part)
    return sec

# ---------- beats: find the song's pulse so moves and the ending can land on it ----------
def find_beats(path, start, dur, bpm=None):
    """Return (beat times, downbeat times, bpm), times relative to the clip start."""
    sr, hop = 11025, 256
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-i", path, "-t", str(dur), "-ac", "1",
                          "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    nfr = (len(x) - 1024) // hop
    win = np.hanning(1024).astype(np.float32)
    spec = np.abs(np.fft.rfft(np.stack([x[i * hop:i * hop + 1024] * win for i in range(nfr)]), axis=1))
    def onset(sp):
        f = np.maximum(0, np.diff(np.log1p(sp * 10), axis=0)).sum(1)
        return np.concatenate([[0], f - np.convolve(f, np.ones(16) / 16, "same")]).clip(0)
    flux = onset(spec)
    low = onset(spec[:, :int(200 / (sr / 1024))])                      # bass band, for finding bar starts
    fps = sr / hop
    if bpm:
        lag = fps * 60 / bpm
    else:
        lags = np.arange(int(fps * 60 / 180), int(fps * 60 / 60))
        ac = np.array([np.dot(flux[:-l], flux[l:]) for l in lags])
        prior = np.exp(-0.5 * (np.log2(60 * fps / lags / 100) / 0.6) ** 2)   # favour ~100 bpm
        lag = lags[np.argmax(ac * prior)]
    def comb(p, o, env):
        idx = np.arange(o, len(env) - 1, p).round().astype(int)
        return env[idx].sum() + 0.5 * env[np.clip(idx + 1, 0, len(env) - 1)].sum()
    fn, ln = flux / (flux.mean() + 1e-9), low / (low.mean() + 1e-9)    # bass helps pick on-beats over off-beats
    periods = [lag] if bpm else np.linspace(lag - 1, lag + 1, 81)
    _, period, o = max((comb(p, o, fn) + comb(p, o, ln), p, o) for p in periods for o in np.arange(0, p, 0.25))
    bpm = 60 * fps / period
    beats = np.arange(o, len(flux) - 1, period) / fps + 0.05         # onset frames lag the true hit slightly
    k = int(np.argmax([comb(period * 4, o + j * period, low) for j in range(4)]))   # strongest bass phase = downbeat
    return beats, beats[k::4], bpm

# ---------- timeline: which source position each output frame shows ----------
def build_timeline(n_src, reading_end, a, downbeats):
    """Returns list of (source position, show captions, forward?) and the time the ending starts."""
    end_at = reading_end - 0.3 + a.pause                                  # straight into the ending as the last word closes
    if a.ending == "rewind" and downbeats is not None and a.beat_rewind:
        near = [b for b in downbeats if -0.4 <= b - end_at <= 0.6]
        if near: end_at = min(near, key=lambda b: abs(b - end_at))        # nudge onto a downbeat, never a long wait
    fwd = int(round(end_at * FPS))
    if a.ending == "hold": fwd += int(round(a.hold_seconds * FPS))
    if a.fit == "realtime":
        pos = [min(float(i), n_src - 1) for i in range(fwd)]
    else:   # the whole span across the reading: speed adapts to the passage length
        pos = list(np.linspace(0, n_src - 1, fwd))
    tl = [(p, i / FPS < reading_end + 0.5, True) for i, p in enumerate(pos)]
    last = pos[-1]
    if a.ending == "rewind":
        nr = int(round(a.rewind_seconds * FPS))
        for j in range(1, nr + 1):                                         # the last line fades out over the rewind's start
            tl.append((last * (1 - smooth(j / nr)), (fwd + j) / FPS < reading_end + 0.5, False))          # ease out, race back, settle on frame 0
        tl += [(0.0, False, False)] * int(round(a.hold_seconds * FPS))
    return tl, end_at

# ---------- audio: everything in numpy so levels are exact and predictable ----------
def load_audio(path, start=0.0, dur=None):
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-i", path]
    if dur: cmd += ["-t", str(dur)]
    cmd += ["-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()

def db(x): return 10 ** (x / 20)

def fit(a, n):
    return a[:n] if len(a) >= n else np.vstack([a, np.zeros((n - len(a), 2), np.float32)])

def has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(r.stdout.strip())

def build_audio(voice, out_wav, total_s, reading_end, a, rewind_at, card_at=None):
    """Voice from t=0. Music from t=0 at one steady level, rising after the reading, fading at the very end.
    The end card (if any) starts at card_at: the music carries on through it, unless --end-card-sound says
    "own" (the card's own sound, music fades out) or names a sound file (played on top of the music)."""
    n = int(total_s * SR)
    mix = fit(load_audio(voice), n) if voice else np.zeros((n, 2), np.float32)
    if a.music:
        m = fit(load_audio(a.music, a.music_start, total_s + 1), n)
        t = np.arange(n) / SR
        gain = db(a.music_db) * (1 + (db(a.end_boost_db) - 1) * np.clip((t - reading_end) / 0.6, 0, 1))
        m *= gain[:, None].astype(np.float32)
        m[:int(0.01 * SR)] *= np.linspace(0, 1, int(0.01 * SR))[:, None]   # 10 ms de-click only, no fade-in
        if card_at is not None and a.end_card_sound == "own" and has_audio(a.end_card):
            i, f = int(card_at * SR), int(0.5 * SR)
            m[i:i + f] *= np.linspace(1, 0, len(m[i:i + f]))[:, None]; m[i + f:] = 0
        f = int(0.8 * SR); m[-f:] *= np.linspace(1, 0, f)[:, None]
        mix += m
    if a.rewind_sfx and rewind_at is not None:          # optional sound of your own, on top of the music
        i = int(rewind_at * SR)
        fx = load_audio(a.rewind_sfx)[: n - i] * db(a.music_db + a.end_boost_db)
        mix[i:i + len(fx)] += fx
    if card_at is not None and a.end_card_sound != "music":
        i = int(card_at * SR)
        if a.end_card_sound == "own":
            if has_audio(a.end_card): snd = load_audio(a.end_card)[: n - i]
            else: snd = np.zeros((0, 2), np.float32)
        else:
            snd = load_audio(a.end_card_sound)[: n - i] * db(a.music_db + a.end_boost_db)
        mix[i:i + len(snd)] += snd
    peak = np.abs(mix).max()
    if peak > 0.98: mix *= 0.98 / peak                  # one fixed scale for the whole clip, never pumping
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())

def mux(video, audio_wav, out, cover=None):
    """Video + mix -> out. A cover image is embedded as the file's thumbnail (what Finder/Photos show)."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video, "-i", audio_wav]
    if cover: cmd += ["-i", cover]
    cmd += ["-map", "0:v", "-map", "1:a"]
    if cover: cmd += ["-map", "2", "-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]
    cmd += ["-c:v:0", "copy", "-c:a", "aac", "-b:a", "192k"] + ([] if cover else ["-shortest"])   # -shortest would stop at the one-frame cover
    cmd += ["-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)

# ---------- end card: your own image, GIF or video after the ending ----------
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")

def card_seconds(a):
    if a.end_card_seconds: return a.end_card_seconds
    if a.end_card.lower().endswith(IMAGE_EXT): return 3.0
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", a.end_card],
                       capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return 3.0          # some GIFs report no length

def card_frames(a, secs):
    """The card scaled to fill the frame (cropping any overflow), as RGB frames at 30 fps."""
    n = int(round(secs * FPS))
    still = a.end_card.lower().endswith(IMAGE_EXT)
    cmd = ["ffmpeg", "-v", "error"] + (["-loop", "1"] if still else ["-stream_loop", "-1"]) + \
          ["-t", str(secs), "-i", a.end_card, "-vf",
           f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=rgb24",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    if not len(fr): raise SystemExit(f"could not read the end card {a.end_card}")
    if len(fr) < n: fr = np.concatenate([fr, np.repeat(fr[-1:], n - len(fr), 0)])
    return fr[:n]

# ---------- cover: the most dramatic moment of the clip, as a still ----------
def drama(a):
    """Higher for vivid, warm, contrasty skies (peak sunset, golden hour, lit-up clouds); dull or dark is low."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    colorful = np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())   # Hasler & Suesstrunk
    mx, mn = a.max(-1), a.min(-1)
    sat = (mx - mn) / (mx + 1)
    warm = ((r >= g) & (r - b > 50) & (sat > 0.35) & (mx > 90))                      # orange, red, pink light
    sky = len(warm) // 2
    warm = 0.7 * warm[:sky].mean() + 0.3 * warm[sky:].mean()                          # counts most in the sky
    lum = a.mean(-1)
    return colorful + 60 * warm + 0.25 * lum.std() - max(0, 60 - lum.mean())

def pick_cover(paths, tl, last_fwd, move, samples=240):
    """Source position of the most dramatic forward frame, scored on small previews in the clip's framing."""
    fwd = [p for p, _, f in tl if f]
    picks = sorted({int(round(fwd[int(i)])) for i in np.linspace(0, len(fwd) - 1, min(samples, len(fwd)))})
    best = (-1e9, 0)
    for k in picks:
        im = Image.open(paths[k]); full_w = im.height     # upright width (frames are stored sideways)
        im.draft("RGB", (im.width // 8, im.height // 8))  # JPEG decodes at 1/8 size: fast
        im = im.transpose(ROTATE).convert("RGB")
        sc = full_w / im.width
        x0, y0, x1, y1 = crop_box(im.width * sc, im.height * sc, k / max(1, last_fwd), move)
        a = np.asarray(im.crop((int(x0 / sc), int(y0 / sc), int(x1 / sc), int(y1 / sc))), np.float32)
        best = max(best, (drama(a), k))
    return best[1]

# ---------- frames ----------
class Frames:
    """Decoded upright frames with a small cache, per-frame deflicker gains and the light-trail buffer."""
    def __init__(self, paths, deflicker=True):
        self.paths, self.cache = paths, {}
        self.gain = np.ones((len(paths), 3), np.float32)
        if deflicker:
            from concurrent.futures import ThreadPoolExecutor
            def mean(p):
                im = Image.open(p); im.draft("RGB", (336, 190))
                return np.asarray(im.convert("RGB"), np.float32).mean((0, 1))
            with ThreadPoolExecutor(8) as ex:
                m = np.array(list(ex.map(mean, paths)))
            k = 31                                       # ~4 min of real time at 8 s per frame
            pad = np.pad(m, ((k // 2, k // 2), (0, 0)), mode="edge")
            ref = np.stack([np.convolve(pad[:, c], np.ones(k) / k, "valid") for c in range(3)], 1)
            self.gain = np.clip(ref / np.maximum(m, 1), 0.85, 1.15).astype(np.float32)
        self.trail, self.trail_at = None, -1

    def get(self, k):
        if k not in self.cache:
            if len(self.cache) > 12: self.cache.pop(next(iter(self.cache)))
            a = np.asarray(Image.open(self.paths[k]).transpose(ROTATE).convert("RGB"), np.float32)
            self.cache[k] = a * self.gain[k]
        return self.cache[k]

    def window(self, pos, width):
        """Triangle-weighted blend of the source frames around pos covering `width` frames (>= 1)."""
        n = len(self.paths)
        if width <= 1.01:
            i0 = int(np.floor(pos)); f = pos - i0
            if f < 0.02 or i0 + 1 >= n: return self.get(min(i0, n - 1))
            if f > 0.98: return self.get(i0 + 1)
            return self.get(i0) * (1 - f) + self.get(i0 + 1) * f
        ks = np.arange(int(np.ceil(pos - width)), int(np.floor(pos + width)) + 1)
        ks = ks[(ks >= 0) & (ks < n)]
        if len(ks) > 9: ks = np.unique(np.linspace(ks[0], ks[-1], 9).round().astype(int))
        wts = np.maximum(0.05, 1 - np.abs(ks - pos) / width)
        acc = sum(self.get(int(k)) * w for k, w in zip(ks, wts))
        return acc / wts.sum()

    def trails(self, upto, decay):
        """Lights leave fading streaks: running max of the frames played so far, fading each frame."""
        upto = int(upto)
        if self.trail is None or upto < self.trail_at:
            self.trail, self.trail_at = self.get(upto).copy(), upto
        while self.trail_at < upto:
            self.trail_at += 1
            np.maximum(self.trail * decay, self.get(self.trail_at), out=self.trail)
        return self.trail

DIRECTIVE_FLAGS = {"style": "style", "move": "move", "aspect": "aspect", "fit": "fit", "ending": "ending",
                   "end_card": "end_card", "end_card_seconds": "end_card_seconds", "end_card_sound": "end_card_sound",
                   "cover": "cover", "safe": "safe", "contrast": "contrast",
                   "music": "music", "music_start": "music_start", "music_db": "music_db", "end_boost_db": "end_boost_db",
                   "trails": "trails", "exposure": "exposure", "fx_every": "fx_every", "beat_punch": "beat_punch",
                   "pause": "pause", "rewind_seconds": "rewind_seconds"}

def apply_directives(a, ap):
    """"@name value" lines at the top of the passage set the same options as the command line."""
    if not a.text: return
    for k, v in markup.directives(open(a.text, encoding="utf-8").read()).items():
        if k in ("effects",) and v.strip().lower() in ("off", "no", "none"):
            a.no_auto_fx = True
        elif k in DIRECTIVE_FLAGS:
            act = next(x for x in ap._actions if x.dest == DIRECTIVE_FLAGS[k])
            setattr(a, act.dest, act.type(v) if act.type else v)
            if act.choices and getattr(a, act.dest) not in act.choices:
                raise SystemExit(f"@{k} {v}: choose from {', '.join(map(str, act.choices))}")

def main(a):
    global W, H, STYLE, SAFE, CONTRAST
    W, H = (1920, 1080) if a.aspect == "16x9" else (1080, 1920)
    m = SAFE_9x16 if a.aspect == "9x16" and a.safe == "on" else dict(left=0, top=0, right=0, bottom=0)
    SAFE = (m["left"], m["top"], W - m["right"], H - m["bottom"])
    CONTRAST = a.contrast == "on"
    if a.end_card:
        a.end_card = a.end_card.replace("{aspect}", a.aspect)   # card_{aspect}.png -> card_9x16.png / card_16x9.png
        if not os.path.exists(a.end_card): raise SystemExit(f"end card not found: {a.end_card}")
    STYLE = styles.get(a.style, seed=os.path.basename(a.out).rsplit("_", 1)[0])
    if a.aspect == "16x9":       # long form: wider, smaller lines lower in the frame, more words per line
        STYLE.update(size=STYLE["size"] * 0.7, big=STYLE["big"] * 0.7, max_chars=int(STYLE["max_chars"] * 2),
                     max_words=9, y=0.8, stress_size=STYLE["stress_size"] * 0.9, fx_every=STYLE["fx_every"] * 1.6)
    if a.fx_every is not None: STYLE["fx_every"] = a.fx_every
    print(f"caption style: {STYLE['name']}")
    if a.words:
        words = json.load(open(a.words))
        if a.text: words = markup.apply(words, open(a.text).read())
        phrases = group(words, auto_fx=not a.no_auto_fx)
        reading_end = words[-1]["end"] + 0.3
    else:
        phrases, reading_end = [], a.seconds
    paths = [l.strip() for l in open(a.frames) if l.strip()]
    beats = downbeats = None
    if a.music and (a.beat_punch or a.beat_rewind):
        beats, downbeats, bpm = find_beats(a.music, a.music_start, reading_end + 8, a.bpm)
        print(f"music: {bpm:.1f} bpm, first downbeat {downbeats[0]:.2f}s")
    tl, end_at = build_timeline(len(paths), reading_end, a, downbeats)
    card_s = card_seconds(a) if a.end_card else 0.0
    card_at = len(tl) / FPS if a.end_card else None
    total_s = len(tl) / FPS + int(round(card_s * FPS)) / FPS
    rewind_at = end_at if a.ending == "rewind" else None
    speed = (len(paths) - 1) / max(1, sum(1 for x in tl if x[2]) - 1)
    print(f"{len(paths)} frames over {sum(1 for x in tl if x[2]) / FPS:.1f}s: {speed:.2f} source frames per video frame")
    silent = a.out.replace(".mp4", ".video.mp4")       # kept so audio can be redone without re-rendering
    cover = a.out.replace(".mp4", "_cover.jpg") if a.cover != "none" else None
    last_fwd = max(p for p, _, f in tl if f)

    if not (a.remix and os.path.exists(silent)):
        fr = Frames(paths, deflicker=not a.no_deflicker)
        src_h, src_w = fr.get(0).shape[:2]
        move = MOVES[a.move]
        ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                               "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "18",
                               "-preset", "medium", "-pix_fmt", "yuv420p", silent], stdin=subprocess.PIPE)
        cap_cache, prev = {}, None
        for i, (pos, captions, forward) in enumerate(tl):
            t = i / FPS
            punch = 0.0
            if a.beat_punch and downbeats is not None:
                past = downbeats[downbeats <= t]
                if len(past) and forward:
                    dt = t - past[-1]
                    punch = a.beat_punch * min(1, dt / 0.06) * np.exp(-max(0, dt - 0.06) / 0.35)
            look = plane_look(t, phrases, src_w, src_h, a.plane_zoom) if a.plane_lean else None
            # framing follows the clock going forward, then holds still through the rewind
            box = crop_box(src_w, src_h, pos / max(1, last_fwd) if forward else 1.0, move, punch, look)
            step = abs(pos - prev) if prev is not None else speed
            img = fr.window(pos, max(1.0, step) * (a.exposure if forward else 1))
            if a.trails and forward:
                tr = fr.trails(pos, a.trail_decay)
                img = img + a.trails * (np.maximum(img, tr) - img)
            bg = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC, box=box)
            frame = bg.convert("RGBA")
            if captions and phrases:
                frame = Image.alpha_composite(frame, caption_layer(t, phrases, cap_cache, box, bg=bg))
            last = np.asarray(frame.convert("RGB"))
            ff.stdin.write(last.tobytes())
            prev = pos
        if a.end_card:                                  # quick dissolve from the last frame into your card
            nx = int(0.3 * FPS)
            for j, c in enumerate(card_frames(a, card_s)):
                if j < nx:
                    w = smooth((j + 1) / (nx + 1))
                    c = (last * (1 - w) + c * w).astype(np.uint8)
                ff.stdin.write(np.ascontiguousarray(c).tobytes())
        ff.stdin.close(); ff.wait()
        if cover:
            if a.cover == "auto":
                k = pick_cover(paths, tl, last_fwd, move)
            else:                                       # a time in the clip, e.g. 12.5 or 0:12.5
                i = min(int(parse_time(a.cover) * FPS), len(tl) - 1)
                k = int(round(tl[i][0]))
            img = np.clip(fr.get(k), 0, 255).astype(np.uint8)
            Image.fromarray(img).resize((W, H), Image.BICUBIC, box=crop_box(src_w, src_h, k / max(1, last_fwd), move)) \
                .save(cover, quality=93)
            at = next(i for i, x in enumerate(tl) if x[2] and x[0] >= k) / FPS
            print(f"cover: {cover} (source frame {k}, {at:.1f}s into the clip)")

    wav = a.out.replace(".mp4", ".mix.wav")
    build_audio(a.voice, wav, total_s, reading_end, a, rewind_at, card_at)
    mux(silent, wav, a.out, cover if cover and os.path.exists(cover) else None)
    print(f"{a.out}: {total_s:.1f}s, reading ends {reading_end:.1f}s"
          + (f", rewind {rewind_at:.1f}-{rewind_at + a.rewind_seconds:.1f}s" if rewind_at else ""))

def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames", help="text file listing frame paths in order (the whole span, every frame)")
    ap.add_argument("out")
    ap.add_argument("--words", help="words.json from align.py (omit for a clip with no reading)")
    ap.add_argument("--text", help="the passage with markup (*emphasis*, {plane}, |), matching words.json")
    ap.add_argument("--voice", help="your recording")
    ap.add_argument("--seconds", type=float, default=8.0, help="clip length when there is no reading")
    ap.add_argument("--aspect", choices=["9x16", "16x9"], default="9x16")
    ap.add_argument("--fx-every", type=float, help="about one layout effect every this many seconds "
                    "(default 10 vertical, 16 long form; 0 = only lines you mark)")
    ap.add_argument("--no-auto-fx", action="store_true",
                    help="no automatic layout effects on stanza ends / closing lines ([stairs] etc. in the text still work)")
    ap.add_argument("--style", choices=sorted(styles.STYLES) + ["random"], default="random",
                    help="caption look (styles.py); random picks one per clip name")
    ap.add_argument("--fit", choices=["span", "realtime"], default="span",
                    help="span: the whole frame list plays across the reading; realtime: 1 frame per video frame")
    ap.add_argument("--move", choices=sorted(MOVES), default="push")
    ap.add_argument("--exposure", type=float, default=1.0,
                    help="long-exposure blur: blend this many times the frames each video frame covers")
    ap.add_argument("--trails", type=float, default=0.0, help="light trails strength 0-1")
    ap.add_argument("--trail-decay", type=float, default=0.8, help="how much of a trail survives each source frame")
    ap.add_argument("--plane-lean", action="store_true", help="camera leans in toward a surface caption (off: framing stays put)")
    ap.add_argument("--plane-zoom", type=float, default=1.6, help="how close --plane-lean gets")
    ap.add_argument("--no-deflicker", action="store_true")
    ap.add_argument("--music", help="instrumental audio file (mp3, m4a, wav...)")
    ap.add_argument("--music-start", type=parse_time, default=0.0, help="where in the song to start, e.g. 1:23.5")
    ap.add_argument("--music-db", type=float, default=-16.0, help="steady song level under the voice, dB")
    ap.add_argument("--end-boost-db", type=float, default=6.0, help="how much louder the song gets after the reading")
    ap.add_argument("--bpm", type=float, help="song tempo, if the automatic beat finder gets it wrong")
    ap.add_argument("--beat-punch", type=float, default=0.0, help="zoom bump on each bar's downbeat, e.g. 0.03")
    ap.add_argument("--beat-rewind", action="store_true", help="start the rewind on the next downbeat")
    ap.add_argument("--ending", choices=["rewind", "hold"], default="rewind")
    ap.add_argument("--pause", type=float, default=0.1, help="seconds after the last word before the ending starts")
    ap.add_argument("--rewind-seconds", type=float, default=2.0)
    ap.add_argument("--hold-seconds", type=float, default=0.5, help="end frame length (after rewind, or the hold ending)")
    ap.add_argument("--rewind-sfx", help="optional sound file played at the rewind (none by default)")
    ap.add_argument("--remix", action="store_true", help="reuse the existing render, only redo the audio")
    ap.add_argument("--end-card", help="image, GIF or video shown after the ending, e.g. an author/title card "
                    "(off unless given; made in the clip's shape, 1080x1920 or 1920x1080, anything else is cropped to fill)")
    ap.add_argument("--end-card-seconds", type=float, help="how long the card shows (default: images 3 s, "
                    "GIF/video their own length; longer than a GIF/video loops it)")
    ap.add_argument("--end-card-sound", default="music",
                    help='"music" (the song carries on, default), "own" (the card video\'s own sound; the song fades out) '
                         "or a sound file played on top of the song")
    ap.add_argument("--cover", default="auto", help='"auto" saves the most dramatic frame as <name>_cover.jpg and '
                    'embeds it as the thumbnail; a time like 12.5 uses that moment; "none" skips it')
    ap.add_argument("--safe", choices=["on", "off"], default="on",
                    help="vertical clips: keep flat captions clear of the TikTok/Reels/Shorts buttons and text")
    ap.add_argument("--contrast", choices=["on", "off"], default="on",
                    help="stronger shadow and a soft dark halo when the background behind the words is bright")
    return ap

if __name__ == "__main__":
    ap = parser(); args = ap.parse_args(); apply_directives(args, ap); main(args)
