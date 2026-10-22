"""Opening effects: a sound at the start of the clip paired with a screen effect that follows it.

Each preset in INTROS pairs a sound with a look. The picture starts hidden behind the effect and clears as
the sound passes: the clearing follows the sound's own energy (how much of the gust has been heard), so a
real recording dropped in for a preset stays in sync without retiming anything.

  gust   wind gust sweeping left to right; the picture streaks sideways and settles (motion blur + slide)
  fog    soft breathy wind; the picture emerges from a pale haze, blur dissolving
  rush   fast whoosh; a zoom blur rushes in from the centre and pulls back to the scene
  dark   low swell of air; fades up from black, a little soft at first
  none   no opening effect (the reading starts at t=0, as before)

Sounds: a file in captions/sfx/ named after the preset (sfx/gust.wav, .mp3, .m4a, .aif...) is used if
present; otherwise the sound is synthesized here (placeholder quality: filtered noise). --intro-sound
names any other file. whoosh(), synthesized the same way, can also play under the rewind (--rewind-sfx whoosh).
"""
import glob, os
import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 44100

# seconds: how long the screen effect lasts; lead: when the reading starts (the voice waits for the gust)
INTROS = {
    "gust":  dict(sound="wind",   look="streak", seconds=1.6, lead=1.3, db=-4),
    "fog":   dict(sound="breath", look="haze",   seconds=2.0, lead=1.6, db=-6),
    "rush":  dict(sound="whoosh", look="zoom",   seconds=1.1, lead=0.9, db=-4),
    "dark":  dict(sound="swell",  look="fade",   seconds=1.6, lead=1.4, db=-5),
    "none":  dict(sound=None,     look=None,     seconds=0.0, lead=0.0, db=0),
}

# ---------- sounds ----------
def _noise(n, color, rng):
    """White noise tilted toward pink (color=1) or brown (color=2) with an FFT slope."""
    sp = np.fft.rfft(rng.standard_normal(n))
    f = np.maximum(np.fft.rfftfreq(n, 1 / SR), 20)
    sp = sp / f ** (color / 2)
    sp[f < 35] = 0                              # nothing below 35 Hz: phone speakers can't play it anyway
    x = np.fft.irfft(sp, n)
    return x / np.abs(x).max()

def _sweep(x, lo, hi, shape, q=1.5):
    """Band-pass x with a centre moving lo -> hi along shape (0..1 per sample), in short overlapping blocks
    (zero-padded so the filtering doesn't wrap around and click)."""
    n, blk = len(x), 2048
    pad = blk // 2                              # the zero-phase filter rings both ways: room before and after
    out, win = np.zeros(n + 2 * blk), np.hanning(blk + 1)[:blk]
    f = np.fft.rfftfreq(2 * blk, 1 / SR)
    for i in range(0, n - blk, blk // 2):
        c = lo * (hi / lo) ** shape[i + blk // 2]
        sp = np.fft.rfft(x[i:i + blk] * win, 2 * blk)
        sp *= np.exp(-0.5 * (np.log2(np.maximum(f, 1) / c) * q) ** 2)
        out[i:i + 2 * blk] += np.roll(np.fft.irfft(sp, 2 * blk), pad)
    return out[pad:pad + n]

def _env(t, attack, peak_at, release):
    """Rise to 1 at peak_at, decay after; attack/release are time constants in seconds."""
    up = np.clip(t / peak_at, 0, 1) ** attack
    down = np.exp(-np.maximum(0, t - peak_at) / release)
    return up * down

def synth(kind, seed=7):
    """Placeholder sound effects, stereo float32 at 44.1 kHz. Swap for real recordings via captions/sfx/."""
    rng = np.random.default_rng(seed)
    if kind == "wind":           # a gust: rises, howls through the mids, passes left to right
        d = 2.6; t = np.arange(int(d * SR)) / SR
        sh = _env(t, 1.6, 0.75, 0.5)
        x = _sweep(_noise(len(t), 1.2, rng), 180, 1400, sh, q=1.2) * sh
        x += 0.35 * _sweep(_noise(len(t), 2, rng), 60, 160, sh, q=0.8) * sh       # body
        pan = np.clip(t / 1.4, 0, 1)
    elif kind == "breath":       # soft and airy, slower
        d = 3.2; t = np.arange(int(d * SR)) / SR
        sh = _env(t, 1.2, 0.7, 0.8)
        x = _sweep(_noise(len(t), 0.6, rng), 900, 3000, sh, q=0.9) * sh
        pan = 0.5 + 0.15 * np.sin(t * 2)
    elif kind == "whoosh":       # quick pass-by
        d = 1.4; t = np.arange(int(d * SR)) / SR
        sh = _env(t, 2.5, 0.42, 0.22)
        x = _sweep(_noise(len(t), 1, rng), 250, 4000, sh, q=1.4) * sh
        pan = np.clip((t - 0.1) / 0.6, 0, 1)
    elif kind == "swell":        # low air swelling up from silence
        d = 2.8; t = np.arange(int(d * SR)) / SR
        sh = _env(t, 1.5, 0.9, 0.6)
        x = _sweep(_noise(len(t), 2, rng), 50, 300, sh, q=0.7) * sh
        x += 0.3 * _sweep(_noise(len(t), 1, rng), 400, 1200, sh, q=1) * sh
        pan = np.full(len(t), 0.5)
    else:
        raise SystemExit(f"unknown sound {kind}")
    x /= np.abs(x).max()
    st = np.stack([x * np.cos(pan * np.pi / 2), x * np.sin(pan * np.pi / 2)], 1) * 1.3
    return np.clip(st, -1, 1).astype(np.float32)

def sound_file(preset):
    """captions/sfx/<preset>.* if you've added one."""
    for p in sorted(glob.glob(os.path.join(HERE, "sfx", preset + ".*"))):
        if not p.endswith((".txt", ".md")): return p
    return None

def clearing(audio, frames, fps=30):
    """How hidden the picture is at each video frame of the intro (1 = fully, 0 = clear): what's left of
    the sound's energy, so the picture clears as the gust passes."""
    e = np.abs(audio.astype(np.float64)).sum(1)
    c = np.cumsum(e) / max(e.sum(), 1e-9)
    idx = np.minimum((np.arange(frames) / fps * SR).astype(int), len(c) - 1)
    k = 1 - c[idx]
    out = np.clip((frames - 1 - np.arange(frames)) / max(1, frames * 0.5), 0, 1)   # over the last half, ease to clear
    return k * out * out * (3 - 2 * out)

# ---------- looks: applied to the frame (PIL RGB, W x H) before captions ----------
def streak(img, k, t):
    """Sideways motion blur that thins out, with the picture sliding in from the left and settling."""
    W, H = img.size
    if k < 0.01: return img
    z = 1 + 0.12 * k                             # a touch closer, so the slide never shows an edge
    c = (W - W / z) / 2
    ox, oy = c * (1 + k), (H - H / z) / 2        # picture sits to the left at first, glides to centre
    img = img.resize((W, H), Image.BICUBIC, box=(ox, oy, ox + W / z, oy + H / z))
    n = max(1, int(W / (1 + 60 * k)))           # squash sideways, stretch back: a cheap horizontal blur
    out = img.resize((n, H), Image.BOX).resize((W, H), Image.BICUBIC)
    a = np.asarray(out, np.float32)
    lift = 18 * k                                # a little airy brightening at the peak
    return Image.fromarray(np.clip(a + lift, 0, 255).astype(np.uint8))

def haze(img, k, t):
    """Pale fog over the picture, blurred, both thinning out."""
    if k < 0.01: return img
    k = k ** 1.6                                 # starts thinning early, the last wisps linger
    b = img.filter(ImageFilter.GaussianBlur(28 * k))
    a = np.asarray(b, np.float32)
    fog = np.array([214, 218, 224], np.float32)
    return Image.fromarray(np.clip(a + (fog - a) * (0.8 * k), 0, 255).astype(np.uint8))

def zoom(img, k, t):
    """Radial zoom blur: copies scaled up around the centre, averaged; starts close and pulls back."""
    if k < 0.01: return img
    W, H = img.size
    acc, wsum = np.zeros((H, W, 3), np.float32), 0.0
    base = 1 + 0.25 * k
    for j in range(12):
        z = base * (1 + 0.03 * k * j)
        cw, ch = W / z, H / z
        box = ((W - cw) / 2, (H - ch) / 2, (W + cw) / 2, (H + ch) / 2)
        w = 1.0 / (1 + j)
        acc += np.asarray(img.resize((W, H), Image.BILINEAR, box=box), np.float32) * w; wsum += w
    return Image.fromarray(np.clip(acc / wsum, 0, 255).astype(np.uint8))

def fade(img, k, t):
    """Up from black, softened at first."""
    if k < 0.01: return img
    if k > 0.3: img = img.filter(ImageFilter.GaussianBlur(10 * (k - 0.3)))
    a = np.asarray(img, np.float32) * (1 - k) ** 1.5
    return Image.fromarray(a.astype(np.uint8))

LOOKS = {"streak": streak, "haze": haze, "zoom": zoom, "fade": fade}

class Intro:
    """One clip's opener: the sound (stereo float32) and the per-frame look."""
    def __init__(self, preset, load, sound=None, db=None, seconds=None, lead=None, fps=30):
        """load(path) -> stereo float32 at 44.1 kHz (render.load_audio)."""
        if preset not in INTROS: raise SystemExit(f"intro {preset}: choose from {', '.join(INTROS)}")
        p = INTROS[preset]
        self.preset = preset
        self.seconds = p["seconds"] if seconds is None else seconds
        self.lead = p["lead"] if lead is None else lead
        self.db = p["db"] if db is None else db
        self.look = LOOKS.get(p["look"])
        self.source = sound or sound_file(preset) or (p["sound"] and f"synth:{p['sound']}")
        self.audio = None
        if self.source and self.source.startswith("synth:"):
            self.audio = synth(self.source[6:])
        elif self.source:
            self.audio = load(self.source)
        n = int(round(self.seconds * fps))
        self.k = clearing(self.audio, n, fps) if self.audio is not None and n else np.zeros(n)

    def frame(self, img, i):
        """img: PIL RGB frame number i of the clip."""
        if self.look is None or i >= len(self.k): return img
        return self.look(img, float(self.k[i]), i)

def lead_for(preset, lead=None):
    """Seconds the reading waits for the opener (batch.py uses this to size the footage span)."""
    if lead not in (None, ""): return float(lead)
    return INTROS.get(preset or "none", INTROS["none"])["lead"]

if __name__ == "__main__":       # write the placeholder sounds so you can hear them: python intro.py out_dir
    import sys, wave
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    for name in ("wind", "breath", "whoosh", "swell"):
        a = synth(name)
        with wave.open(os.path.join(out, f"{name}.wav"), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((a * 32767).astype(np.int16).tobytes())
        print(f"{name}.wav  {len(a) / SR:.1f}s")
