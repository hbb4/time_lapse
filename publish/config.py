"""Settings, paths and secrets for the publisher.

Secrets come from the environment (or a .env file in ~/.config/time_lapse/publish/ or the repo root, never committed).
Everything else has a default here and can be overridden in ~/.config/time_lapse/publish/settings.toml
(see settings.example.toml).
"""
import copy, os, tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("TL_PUBLISH_HOME", Path.home() / ".config" / "time_lapse" / "publish"))
STATE = Path(os.environ.get("TL_PUBLISH_STATE", Path.home() / ".local" / "share" / "time_lapse" / "publish"))
DB_PATH = STATE / "publish.db"
TOKENS_PATH = HOME / "tokens.json"
WORK_DIR = STATE / "work"                 # captioned renders, transcripts, universal exports (safe to delete)

PLATFORMS = ["youtube", "tiktok", "instagram", "facebook", "x"]

# Limits are conservative defaults taken from each platform's public docs; they change, so override any of
# them under [limits.<platform>] in settings.toml rather than editing here. TikTok's own cap is read live
# from the API (creator_info) at post time.
DEFAULTS = {
    "platforms": PLATFORMS,
    "privacy": "public",                  # public | unlisted | private (unlisted = private where a platform has no such thing)
    "hashtags": [],                       # added to every post, e.g. ["timelapse", "sanfrancisco"]
    "max_hashtags": {"youtube": 3, "tiktok": 5, "instagram": 5, "facebook": 3, "x": 2},
    "cta": "",                            # one line added under every description, e.g. "More at example.com"
    "stagger_seconds": 0,                 # wait between platforms so posts don't all land in the same second
    "retries": 3,                         # whole-post attempts per platform before it is marked failed
    "meta_api_version": "v23.0",
    "tiktok_mode": "direct",              # direct = publish now; draft = lands in your TikTok inbox to finish by hand
    "youtube_category": "22",             # 22 = People & Blogs, 24 = Entertainment, 28 = Science & Technology
    "youtube_made_for_kids": False,
    "limits": {
        "youtube":   {"max_seconds": 43200, "short_seconds": 180, "title": 100, "description": 5000, "max_bytes": 256 * 2**30},
        "tiktok":    {"max_seconds": 600, "caption": 2200, "max_bytes": 4 * 2**30},
        "instagram": {"max_seconds": 90, "caption": 2200, "max_bytes": 1 * 2**30},
        "facebook":  {"max_seconds": 90, "reel_description": 2200, "description": 5000, "max_bytes": 10 * 2**30},
        "x":         {"max_seconds": 140, "text": 280, "max_bytes": 512 * 2**20},
    },
    "captions": {"mode": "auto", "style": "auto", "edit": "subtle", "cut_silence": False, "backend": "faster",
                 "model": None, "language": None, "music": None},
}

def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict): _merge(base[k], v)
        else: base[k] = v
    return base

def load_env():
    """Read KEY=VALUE lines from the .env files into os.environ (real environment variables win)."""
    for p in (HOME / ".env", REPO / ".env"):
        if not p.exists(): continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

def settings():
    load_env()
    s = copy.deepcopy(DEFAULTS)
    p = HOME / "settings.toml"
    if p.exists(): _merge(s, tomllib.loads(p.read_text()))
    return s

def env(name, default=None):
    load_env()
    return os.environ.get(name, default)
