from .facebook import Facebook
from .instagram import Instagram
from .tiktok import TikTok
from .x import X
from .youtube import YouTube

REGISTRY = {c.name: c for c in (YouTube, TikTok, Instagram, Facebook, X)}
