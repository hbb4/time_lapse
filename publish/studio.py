"""The caption-and-edit step: hands a video to captions/burn.py and keeps the transcript, cover and subtitles."""
import json, sys
from pathlib import Path

from . import config

def _burn():
    d = str(config.REPO / "captions")
    if d not in sys.path: sys.path.insert(0, d)
    import burn
    return burn

def plain(text):
    """The words of a marked-up passage (*stress*, {plane}, @ lines removed)."""
    _burn()
    import markup
    return markup.plain(text)

def caption(video, work, mode, c, text=None, log=print):
    """Burn captions into `video`. mode: auto (do it) | done (already captioned) | off. `c` = the [captions] settings.
    Returns dict(video, cover, srt, words) or None when no new video was made."""
    video, work = Path(video), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    words_p = work / "words.json"
    words = json.loads(words_p.read_text()) if words_p.exists() else None
    if mode != "auto":                       # already captioned (e.g. by captions/render.py): keep its cover if it made one
        cover = video.with_name(video.stem + "_cover.jpg")
        return dict(video=video, cover=cover if cover.exists() else None, srt=None, words=words)
    out, cover, srt = work / f"{video.stem}.captioned.mp4", work / "cover.jpg", work / "subtitles.srt"
    if out.exists() and words:
        log(f"captions: reusing {out.name}")
        return dict(video=out, cover=cover if cover.exists() else None, srt=srt if srt.exists() else None, words=words)
    burn = _burn()
    r = burn.burn(str(video), str(out), words=words, text=text, style=c["style"], edit=c["edit"], cut_silence=c["cut_silence"],
                  aspect=c.get("aspect", "auto"), music=c.get("music"), cover=str(cover), srt=str(srt), backend=c["backend"],
                  model=c["model"], language=c["language"], log=log)
    words_p.write_text(json.dumps(r["words"], indent=1))
    return dict(video=out, cover=cover if cover.exists() else None, srt=srt if srt.exists() else None, words=r["words"])
