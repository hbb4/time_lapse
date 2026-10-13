"""Post text for each platform: a title, a description, a short post, hashtags, all inside that platform's limits.

Sources, in order of priority: what you pass on the command line or in the video's sidecar
(`clip.post.toml` next to `clip.mp4`), then the transcript (first sentence = hook, frequent words = hashtags).
"""
import re, tomllib
from collections import Counter
from pathlib import Path

STOP = set("""about above after again also always because been before being between both could does doing down during each
even ever every from further have having here into just like made make many more most much must never only other over
really same should since some still such than that their them then there these they thing things this those through
very want were what when where which while will with without would your you're it's that's there's""".split())

def sidecar(video):
    p = Path(video).with_suffix(".post.toml")
    return tomllib.loads(p.read_text()) if p.exists() else {}

def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s.strip()]

def trim(s, n, ellipsis="…"):
    """At most n characters, cut at a word boundary."""
    s = s.strip()
    if len(s) <= n: return s
    cut = s[: n - len(ellipsis)].rsplit(" ", 1)[0].rstrip(" ,;:-—")
    return (cut or s[: n - len(ellipsis)]) + ellipsis

def keywords(text, n=3):
    toks = [t for t in re.findall(r"[a-z']{5,}", text.lower()) if t not in STOP]
    return [w.replace("'", "") for w, _ in Counter(toks).most_common(n)]

def hashtags(settings, text="", extra=(), n=5):
    seen, out = set(), []
    for t in list(extra) + list(settings.get("hashtags", [])) + keywords(text):
        t = re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if t and t.lower() not in seen: seen.add(t.lower()); out.append(t)
    return out[:n]

def build(platform, kind, settings, text="", title=None, description=None, short=None, tags=(), link="", overrides=None):
    """{title, description, text, tags} for one platform. `overrides` is that platform's table from the sidecar."""
    o = overrides or {}
    lim = settings["limits"][platform]
    sents = sentences(text)
    hook = title or (sents[0] if sents else "")
    rest = sents[1:5] if not title and sents else sents[:4]       # the hook is not repeated in the body
    body = description if description is not None else " ".join(rest)
    tagn = settings["max_hashtags"].get(platform, 3)
    tg = hashtags(settings, text, tags, tagn)
    tagline = " ".join("#" + t for t in tg)
    cta = " ".join(x for x in (settings.get("cta", ""), link) if x)
    tail = "\n\n".join(x for x in (cta, tagline) if x)
    r = dict(title="", description="", text="", tags=tg)
    if platform == "youtube":
        suffix = " #Shorts" if kind == "short" else ""
        r["title"] = trim(o.get("title") or hook or "Untitled", lim["title"] - len(suffix)) + suffix
        r["description"] = trim(o.get("description") or "\n\n".join(x for x in (body, tail) if x), lim["description"])
        r["tags"] = [t for t in tg]
    elif platform in ("tiktok", "instagram"):
        cap = o.get("text") or short or (hook if not body or hook == body else f"{hook}\n\n{body}")
        r["text"] = _with_tail(cap, tail, lim["caption"])
    elif platform == "facebook":
        key = "reel_description" if kind == "reel" else "description"
        cap = o.get("description") or (hook if not body or hook == body else f"{hook}\n\n{body}")
        r["text"] = _with_tail(cap, tail, lim[key]); r["title"] = trim(o.get("title") or hook, 255)
    elif platform == "x":
        cap = o.get("text") or short or hook
        x_tail = " ".join(x for x in ([link] if link else []) + ["#" + t for t in tg])
        r["text"] = _with_tail(cap, x_tail, lim["text"], sep=" ", url_len=23 if link else 0, link=link)
    return r

def _with_tail(body, tail, limit, sep="\n\n", url_len=0, link=""):
    """body + tail, with the body shortened first when the two don't fit (a link counts 23 on X)."""
    def size(s): return len(s.replace(link, "x" * url_len)) if link else len(s)
    if not tail: return trim(body, limit)
    room = limit - size(tail) - len(sep)
    if room < 20: return trim(body, limit)          # no space for the tail; the words matter more than the tags
    return trim(body, room) + sep + tail
