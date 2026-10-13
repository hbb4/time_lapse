"""Prepare a local video (caption, export, route, write the copy), queue it, and post it."""
import json, time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config, export, studio, writer
from .http import Http
from .ledger import Ledger
from .models import Job
from .platforms import REGISTRY
from .tokens import TokenStore

PRIVATE_OK = {"youtube", "tiktok"}      # the only destinations with a private mode: anything else would go out public

@dataclass
class Opts:
    platforms: list | None = None
    captions: str | None = None          # auto | done | off
    style: str | None = None
    edit: str | None = None
    cut_silence: bool | None = None
    music: str | None = None
    text_file: str | None = None         # the passage, when you have one: used for captions and for the post copy
    title: str | None = None
    description: str | None = None
    short: str | None = None             # the short post (X, TikTok, Instagram) if you want to write it yourself
    hashtags: list = field(default_factory=list)
    link: str | None = None
    privacy: str | None = None
    publish_at: str | None = None        # "2026-10-14 18:00" local time
    dry_run: bool = False
    force: bool = False

def when(s):
    return datetime.fromisoformat(s.strip().replace(" ", "T")).timestamp() if s else None

def settings_for(o, side):
    """Settings with the sidecar and then the command line layered on top."""
    s = config.settings()
    c = s["captions"]
    for src in (side, vars(o)):
        for k in ("style", "edit", "cut_silence", "music"):
            if src.get(k) is not None: c[k] = src[k]
    mode = o.captions or side.get("captions")
    if mode: c["mode"] = mode
    return s

def decide(media, o, side, s, transcript=""):
    """Which platforms get this video, as what, with what words. Pure: shared by `plan` and `ship`.
    Returns (kinds, texts, skipped, privacy)."""
    wanted = o.platforms or side.get("platforms") or s["platforms"]
    privacy = o.privacy or side.get("privacy") or s["privacy"]
    skipped = {}
    if privacy != "public":
        for p in [p for p in wanted if p not in PRIVATE_OK]: skipped[p] = f"no private mode on {p}; refusing to post a '{privacy}' video publicly"
        wanted = [p for p in wanted if p in PRIVATE_OK]
    link = o.link or side.get("link", "")
    kinds, texts = {}, {}
    for p, (kind, why) in export.route(media, wanted, s).items():
        if kind is None: skipped[p] = why; continue
        kinds[p] = kind
        texts[p] = writer.build(p, kind, s, transcript, o.title or side.get("title"), o.description or side.get("description"),
                                o.short or side.get("short"), list(o.hashtags) + list(side.get("hashtags", [])), link, side.get(p))
    return kinds, texts, skipped, privacy

def prepare(video, o, log=print):
    """Do all the local work. Returns a plan dict; nothing is posted."""
    video = Path(video).resolve()
    side = writer.sidecar(video)
    s = settings_for(o, side)
    sha = export.sha256(video)
    work = config.WORK_DIR / sha[:12]
    tf = o.text_file or side.get("text_file")
    text = Path(tf).read_text(encoding="utf-8") if tf else None
    cap = studio.caption(video, work, s["captions"]["mode"], s["captions"], text, log)
    source = cap["video"] if cap else video
    media = export.probe(source)
    up, upm = export.universal(source, media, work)
    transcript = (studio.plain(text) if text else None) or (" ".join(w["word"] for w in cap["words"]) if cap and cap.get("words") else "")
    kinds, texts, skipped, privacy = decide(upm, o, side, s, transcript)
    return dict(sha=sha, video=video, upload=up, media=upm, cover=(cap or {}).get("cover"), srt=(cap or {}).get("srt"), kinds=kinds,
                texts=texts, skipped=skipped, privacy=privacy, publish_at=when(o.publish_at or side.get("publish_at")), settings=s)

def spec_of(plan):
    return dict(upload=str(plan["upload"]), cover=str(plan["cover"]) if plan["cover"] else None, srt=str(plan["srt"]) if plan["srt"] else None,
                kinds=plan["kinds"], texts=plan["texts"], privacy=plan["privacy"])

def job_from(spec, settings):
    up = Path(spec["upload"])
    if not up.exists(): raise FileNotFoundError(f"{up} is gone (the work folder was cleaned before this was posted)")
    return Job(up, export.probe(up), spec["texts"], spec["kinds"], spec["privacy"], Path(spec["cover"]) if spec.get("cover") else None,
               Path(spec["srt"]) if spec.get("srt") else None, settings)

def ship(video, o, ledger=None, log=print, run=True, **kw):
    """Prepare, queue and (unless scheduled for later or a dry run) post. Returns the job id, or None for a dry run."""
    plan = prepare(video, o, log)
    ledger = ledger or Ledger()
    todo, skipped = [], dict(plan["skipped"])
    for p in plan["kinds"]:
        prior = None if o.force else ledger.published(plan["sha"], p)
        waiting = None if o.force else ledger.pending(plan["sha"], p, plan["settings"]["retries"])
        if prior: skipped[p] = f"already posted: {prior['url'] or prior['remote_id']} (use --force to post again)"
        elif waiting: skipped[p] = f"already queued as job #{waiting['job_id']} (`publish run` delivers it)"
        else: todo.append(p)
    show_plan(plan, todo, skipped, log)
    if o.dry_run or not todo: return None
    jid = ledger.enqueue(plan["sha"], plan["video"], spec_of(plan), todo, plan["publish_at"], skipped)
    if run and todo and (plan["publish_at"] or 0) <= time.time():
        run_due(ledger, plan["settings"], log=log, **kw)
    return jid

def show_plan(plan, todo, skipped, log):
    m = plan["media"]
    log(f"\n{plan['video'].name}: {m.width}x{m.height}, {m.duration:.1f}s, {'vertical' if m.vertical else 'landscape'}")
    for p in todo:
        t = plan["texts"][p]
        head = t["title"] or (t["text"].splitlines() or [""])[0]
        log(f"  {p:<10} {plan['kinds'][p]:<6} {head[:70]}")
    for p, why in skipped.items(): log(f"  {p:<10} skipped: {why}")

def run_due(ledger, settings, http=None, tokens=None, log=print, registry=None, sleep=time.sleep, now=time.time):
    """Post everything that is queued and due (and retry failures with attempts left). Returns [(platform, status, detail)]."""
    http = http or Http(retries=settings.get("http_retries", 4))
    tokens = tokens or TokenStore()
    registry = registry or REGISTRY
    ledger.recover()
    out, first = [], True
    for row in ledger.due(settings["retries"]):
        p, jid = row["platform"], row["job_id"]
        ad = registry[p](http, tokens, settings, log=lambda m, p=p: log(f"  {m}"), sleep=sleep, now=now)
        miss = ad.missing()
        if miss:
            ledger.fail(jid, p, "not set up: " + ", ".join(miss)); out.append((p, "not set up", ", ".join(miss)))
            log(f"{p}: not set up yet ({', '.join(miss)})"); continue
        if not first and settings.get("stagger_seconds"): sleep(settings["stagger_seconds"])
        first = False
        ledger.start(jid, p)
        try:
            job = job_from(json.loads(row["spec"]), settings)
            if p not in job.kind: raise RuntimeError(f"{p} was not routed for this video")
            r = ad.publish(job)
            ledger.done(jid, p, r.remote_id, r.url, r.note)
            out.append((p, "published", r.url or r.remote_id)); log(f"{p}: published {r.url or r.remote_id}" + (f"  [{r.note}]" if r.note else ""))
        except Exception as e:
            ledger.fail(jid, p, f"{type(e).__name__}: {e}")
            out.append((p, "failed", str(e))); log(f"{p}: FAILED {type(e).__name__}: {str(e)[:300]}")
    return out
