"""Caption looks. Each clip picks one (clips.csv "style" column, or render.py --style); blank = random,
seeded by the clip name so a re-render keeps its look.

Key lines (the last line of a stanza, the closing line, or any line marked [stairs] / [cascade] / [stack] /
[spread] in the passage) get a layout effect from the style's fx list.

A *stressed* word (marked *like this* in the passage) always shows alone on screen while it is spoken,
in the style's stress font and color, with no zoom. Then the next line comes back.

Fonts: each role lists candidates and the first file found is used (macOS system fonts first, then Linux).
To use your own, drop a .ttf/.otf into a "fonts" folder next to this script named after the role,
e.g. fonts/serif.ttf, fonts/stress_serif.otf.
"""
import os, random, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = "/System/Library/Fonts/Supplemental/"
LIN = "/usr/share/fonts/"
FONTS = {
    "heavy":        [MAC + "Arial Black.ttf", LIN + "opentype/inter/InterDisplay-Black.otf", LIN + "truetype/dejavu/DejaVuSans-Bold.ttf"],
    "bold":         [MAC + "Arial Bold.ttf", LIN + "opentype/inter/Inter-ExtraBold.otf", LIN + "truetype/dejavu/DejaVuSans-Bold.ttf"],
    "light":        [MAC + "Trebuchet MS.ttf", LIN + "opentype/inter/Inter-Light.otf", LIN + "opentype/inter/Inter-ExtraLight.otf",
                     LIN + "truetype/dejavu/DejaVuSans.ttf"],
    "serif":        [MAC + "Georgia.ttf", LIN + "truetype/crosextra/Caladea-Regular.ttf", LIN + "truetype/liberation/LiberationSerif-Regular.ttf",
                     LIN + "truetype/dejavu/DejaVuSerif.ttf"],
    "stress_serif": [MAC + "Georgia Bold Italic.ttf", LIN + "truetype/crosextra/Caladea-BoldItalic.ttf",
                     LIN + "truetype/liberation/LiberationSerif-BoldItalic.ttf", LIN + "truetype/dejavu/DejaVuSerif-Bold.ttf"],
    "italic_serif": [MAC + "Georgia Italic.ttf", LIN + "truetype/crosextra/Caladea-Italic.ttf",
                     LIN + "truetype/liberation/LiberationSerif-Italic.ttf", LIN + "truetype/dejavu/DejaVuSerif.ttf"],
    "mono":         [MAC + "Courier New.ttf", LIN + "truetype/liberation/LiberationMono-Regular.ttf", LIN + "truetype/dejavu/DejaVuSansMono.ttf"],
    "mono_bold":    [MAC + "Courier New Bold.ttf", LIN + "truetype/liberation/LiberationMono-Bold.ttf", LIN + "truetype/dejavu/DejaVuSansMono-Bold.ttf"],
    "condensed":    [MAC + "Impact.ttf", LIN + "truetype/liberation/LiberationSansNarrow-Bold.ttf", LIN + "opentype/inter/InterDisplay-Black.otf",
                     LIN + "truetype/dejavu/DejaVuSans-Bold.ttf"],
}

def font(role):
    for ext in (".ttf", ".otf"):
        p = os.path.join(HERE, "fonts", role + ext)
        if os.path.exists(p): return p
    for p in FONTS[role]:
        if os.path.exists(p): return p
    raise SystemExit(f"no font found for '{role}': put one at {os.path.join(HERE, 'fonts', role + '.ttf')}")

BASE = dict(size=104, big=150, max_chars=13, max_lines=2, y=0.66, case=None,
            color=(255, 255, 255), active=(255, 176, 92), stress=(255, 214, 140),
            enter="pop", pop=0.16, hold=0.7, fade=0.22, shadow=0.75, glow=True,
            stress_size=1.6,   # stressed word size relative to the normal size (it is alone on screen)
            stroke=0,          # outline width in px at 1080 wide (black); 0 = none
            lift=0,            # the spoken word grows by this fraction (0.12 = 12 %) while it is active
            fx=["stairs", "stack"],   # layout effects the style cycles through on key lines
            fx_every=10)              # about one effect every this many seconds (long form: x1.6)

STYLES = {
    # bold white words that pop in; the word being spoken turns orange; stress in a gold serif italic
    "pop":     dict(font="heavy", stress_font="stress_serif", fx=["stack", "stairs"]),
    # quiet, bookish: serif words fade in, no highlight; stress in italic, warm amber
    "serif":   dict(font="serif", stress_font="italic_serif", fx=["cascade", "spread"], size=96, big=128, enter="fade", pop=0.35,
                    color=(250, 244, 232), active=(250, 244, 232), stress=(255, 190, 120), glow=False, shadow=0.6),
    # typewriter: letters type out as each word is spoken; stress in bold, red ink
    "type":    dict(font="mono", stress_font="mono_bold", fx=["stairs", "cascade"], size=84, big=110, max_chars=15, enter="type",
                    color=(245, 245, 240), active=(245, 245, 240), stress=(255, 92, 72), glow=False, stress_size=1.5),
    # light lowercase words that rise softly; stress in heavy weight, pale sky blue
    "whisper": dict(font="light", stress_font="heavy", fx=["cascade", "spread"], size=92, big=120, case="lower", enter="rise", pop=0.3,
                    color=(255, 255, 255), active=(200, 228, 255), stress=(170, 215, 255), glow=False),
    # tall condensed capitals that slide up; stress switches to a lowercase serif italic, coral
    "poster":  dict(font="condensed", stress_font="italic_serif", fx=["stack", "stairs"], size=118, big=160, max_chars=12, case="upper",
                    enter="slide", pop=0.2, color=(255, 255, 255), active=(255, 236, 160), stress=(255, 140, 120),
                    stress_case="lower", stress_size=1.9),
    # creator look: heavy capitals with a thick outline; the spoken word turns yellow and swells; stress in green
    "hype":    dict(font="heavy", stress_font="heavy", fx=["stack", "stairs"], size=96, big=140, max_chars=12, max_words=3,
                    case="upper", enter="pop", pop=0.14, color=(255, 255, 255), active=(255, 226, 40), stress=(80, 255, 120),
                    stroke=7, lift=0.05, glow=False, shadow=0.9, stress_size=1.5, y=0.62),
    # clean sans: light lowercase with a gentle highlight, no outline; for calm talking-head or voiceover clips
    "clean":   dict(font="bold", stress_font="bold", fx=["cascade", "spread"], size=88, big=118, max_chars=15, max_words=4,
                    case="lower", enter="rise", pop=0.22, color=(255, 255, 255), active=(120, 220, 255), stress=(255, 255, 255),
                    stroke=0, lift=0.04, glow=False, shadow=0.85),
}

# "random" draws from these only, so adding looks never changes the look of a clip that is re-rendered
RANDOM_POOL = ("pop", "poster", "serif", "type", "whisper")

def get(name=None, seed=""):
    if not name or name == "random":
        name = random.Random(zlib.crc32(seed.encode())).choice(sorted(RANDOM_POOL))
    s = dict(BASE, **STYLES[name])
    s["name"] = name
    s["font_path"], s["stress_path"] = font(s["font"]), font(s["stress_font"])
    return s
