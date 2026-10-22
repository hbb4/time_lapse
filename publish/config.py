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
    # TikTok: draft = the video lands in your TikTok inbox and you tap Post. direct = published by the API, but TikTok only allows
    # that publicly after auditing your app, and its rules want a person confirming each post, so unattended runs stay drafts.
    "tiktok_mode": "draft",
    "tiktok_direct_unattended": False,    # True lets review = "auto"/"delay" use direct posting too (against TikTok's guidelines)
    "x_include_link": False,              # X bills posts that contain a URL at a much higher rate than plain posts
    "youtube_category": "22",             # 22 = People & Blogs, 24 = Entertainment, 28 = Science & Technology
    "youtube_made_for_kids": False,
    "limits": {
        "youtube":   {"max_seconds": 43200, "short_seconds": 180, "title": 100, "description": 5000, "max_bytes": 256 * 2**30},
        "tiktok":    {"max_seconds": 600, "caption": 2200, "max_bytes": 4 * 2**30},
        "instagram": {"max_seconds": 90, "caption": 2200, "max_bytes": 1 * 2**30},
        "facebook":  {"max_seconds": 90, "reel_description": 2200, "description": 5000, "max_bytes": 10 * 2**30},
        "x":         {"max_seconds": 140, "text": 280, "max_bytes": 512 * 2**20},
    },
    # Review: manual = nothing posts until you approve it; delay = posts after delay_minutes unless you reject it; auto = posts at once.
    "review": {"mode": "manual", "delay_minutes": 30},
    # Notifications: channels to use, and each channel's own settings (secrets come from the environment, see .env.example)
    "notify": {"channels": [], "telegram": {"chat_id": ""}, "ntfy": {"topic": "", "server": "https://ntfy.sh"}, "discord": {}, "macos": {}},
    "watch": "",                          # folder `publish watch` picks videos up from for this account (~ allowed)
    "accounts": {},                       # [accounts.NAME] tables: each can override anything here (see settings.example.toml)
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

def settings(account=None):
    """Defaults, then settings.toml, then the [accounts.<account>] table on top when an account is named."""
    load_env()
    s = copy.deepcopy(DEFAULTS)
    p = HOME / "settings.toml"
    if p.exists(): _merge(s, tomllib.loads(p.read_text()))
    if account and account != "default":
        if account not in s["accounts"]:
            raise SystemExit(f"unknown account '{account}': add an [accounts.{account}] table to {p} (known: {', '.join(s['accounts']) or 'none'})")
        _merge(s, copy.deepcopy(s["accounts"][account]))
    s["account"] = account or "default"
    return s

def account_names():
    return ["default"] + [a for a in settings()["accounts"] if a != "default"]

def token_key(platform, account="default"):
    """Where a login is stored: 'x' for the default account, 'x:sf_clouds' for a named one."""
    return platform if not account or account == "default" else f"{platform}:{account}"

def env(name, default=None):
    load_env()
    return os.environ.get(name, default)
