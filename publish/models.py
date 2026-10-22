from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class Media:
    path: Path
    width: int
    height: int
    duration: float
    fps: float
    vcodec: str
    acodec: str | None
    pix_fmt: str
    bytes: int

    @property
    def vertical(self): return self.height > self.width

@dataclass
class Job:
    """Everything an adapter needs to post one video. `texts` holds the finished per-platform copy."""
    video: Path
    media: Media
    texts: dict                      # platform -> {title, description, text, tags}
    kind: dict                       # platform -> short | long | reel | video | post
    privacy: str = "public"
    attended: bool = True            # a person approved this post (review = manual); unattended posts only ever become TikTok drafts
    cover: Path | None = None
    captions: Path | None = None     # .srt
    settings: dict = field(default_factory=dict)

@dataclass
class Result:
    remote_id: str
    url: str | None = None
    note: str | None = None          # e.g. "posted privately: the TikTok app is not audited yet"
