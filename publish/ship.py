"""Prepare a local video (caption, export, route, write the copy), queue it, and post it."""
import json, time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config, export, notify, studio, writer
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
    account: str | None = None           # a name from [accounts.NAME] in settings.toml (default: the unnamed one)
    review: str | None = None            # manual | delay | auto (default from settings: manual)
    publish_at: str | None = None        # "2026-10-14 18:00" local time
    dry_run: bool = False
    force: bool = False

def when(s):
    return datetime.fromisoformat(s.strip().replace(" ", "T")).timestamp() if s else None

def settings_for(o, side):
    """Settings with the sidecar and then the command line layered on top."""
    s = config.settings(o.account)
    c = s["captions"]
    for src in (side, vars(o)):
        for k in ("style", "edit", "cut_silence", "music"):
            if src.get(k) is not None: c[k] = src[k]
    mode = o.captions or side.get("captions")
    if mode: c["mode"] = mode
    review = o.review or side.get("review")
    if review:
        if review not in ("manual", "delay", "auto"): raise SystemExit(f"review must be manual, delay or auto, not '{review}'")
        s["review"]["mode"] = review
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
    return dict(account=s["account"], review=s["review"], sha=sha, video=video, upload=up, media=upm, cover=(cap or {}).get("cover"), srt=(cap or {}).get("srt"), kinds=kinds,
                texts=texts, skipped=skipped, privacy=privacy, publish_at=when(o.publish_at or side.get("publish_at")), settings=s)

def spec_of(plan):
    return dict(upload=str(plan["upload"]), cover=str(plan["cover"]) if plan["cover"] else None, srt=str(plan["srt"]) if plan["srt"] else None,
                kinds=plan["kinds"], texts=plan["texts"], privacy=plan["privacy"], attended=plan["review"]["mode"] == "manual")

def job_from(spec, settings):
    up = Path(spec["upload"])
    if not up.exists(): raise FileNotFoundError(f"{up} is gone (the work folder was cleaned before this was posted)")
    return Job(up, export.probe(up), spec["texts"], spec["kinds"], spec["privacy"], spec.get("attended", True),
               Path(spec["cover"]) if spec.get("cover") else None, Path(spec["srt"]) if spec.get("srt") else None, settings)

def ship(video, o, ledger=None, log=print, run=True, http=None, **kw):
    """Prepare, queue and, depending on the review mode, post. Returns the job id, or None for a dry run / nothing to do.
    manual: posts wait for `publish approve` (or a Telegram tap); delay: they post after review.delay_minutes unless
    rejected; auto: they post at once. Review and publish notifications go to the account's channels."""
    plan = prepare(video, o, log)
    ledger = ledger or Ledger()
    acct, s = plan["account"], plan["settings"]
    todo, skipped = [], dict(plan["skipped"])
    for p in plan["kinds"]:
        prior = None if o.force else ledger.published(plan["sha"], p, acct)
        waiting = None if o.force else ledger.pending(plan["sha"], p, s["retries"], acct)
        if prior: skipped[p] = f"already posted: {prior['url'] or prior['remote_id']} (use --force to post again)"
        elif waiting: skipped[p] = f"already queued as job #{waiting['job_id']} ({waiting['status']})"
        else: todo.append(p)
    show_plan(plan, todo, skipped, log)
    if o.dry_run or not todo: return None
    mode, delay = plan["review"]["mode"], plan["review"]["delay_minutes"]
    at = plan["publish_at"]
    if mode == "delay": at = max(at or 0, time.time() + delay * 60)
    jid = ledger.enqueue(plan["sha"], plan["video"], spec_of(plan), todo, at, skipped, acct, "awaiting" if mode == "manual" else "queued")
    if mode == "manual": log(f"  waiting for your approval: python -m publish approve {jid}")
    elif mode == "delay": log(f"  will post in {delay} min unless you run: python -m publish reject {jid}")
    if mode in ("manual", "delay"):
        http = http or Http()
        preview = export.preview(plan["upload"], plan["upload"].with_suffix(".preview.mp4"))
        notify.notify(s, notify.Event("review" if mode == "manual" else "queued", review_title(plan, jid, mode, delay), review_body(plan, todo, skipped, jid, mode, delay),
                                      acct, jid, preview, plan["cover"], actions=True), http, log)
    if run: run_due(ledger, s, log=log, http=http, **kw)
    return jid

def label(account):
    return "" if account == "default" else f" [{account}]"

def review_title(plan, jid, mode, delay):
    name = plan["video"].name
    return (f"Review #{jid}: {name}" if mode == "manual" else f"Posting in {delay} min: {name}") + label(plan["account"])

def review_body(plan, todo, skipped, jid, mode, delay):
    m = plan["media"]
    lines = [f"{m.width}x{m.height}, {m.duration:.0f}s"]
    for p in todo:
        t = plan["texts"][p]
        lines.append(f"{p} ({plan['kinds'][p]}): {t['title'] or t['text']}"[:300])
    lines += [f"skipped {p}: {why}"[:160] for p, why in skipped.items()]
    lines.append(f"\nApprove: python -m publish approve {jid}   Reject: python -m publish reject {jid}" if mode == "manual"
                 else f"\nReject within {delay} min: python -m publish reject {jid}")
    return "\n".join(lines)

def show_plan(plan, todo, skipped, log):
    m = plan["media"]
    log(f"\n{plan['video'].name}: {m.width}x{m.height}, {m.duration:.1f}s, {'vertical' if m.vertical else 'landscape'}")
    for p in todo:
        t = plan["texts"][p]
        head = t["title"] or (t["text"].splitlines() or [""])[0]
        log(f"  {p:<10} {plan['kinds'][p]:<6} {head[:70]}")
    for p, why in skipped.items(): log(f"  {p:<10} skipped: {why}")

def run_due(ledger, settings, http=None, tokens=None, log=print, registry=None, sleep=time.sleep, now=time.time, notify_fn=notify.notify):
    """Post everything that is queued and due (and retry failures with attempts left), each as its own account. Returns
    [(platform, status, detail)]. One notification per job summarises what happened; a failure that repeats unchanged is not re-sent."""
    http = http or Http(retries=settings.get("http_retries", 4))
    tokens = tokens or TokenStore()
    registry = registry or REGISTRY
    ledger.recover()
    out, first, cfgs, jobs = [], True, {}, {}
    for row in ledger.due(settings["retries"]):
        p, jid, acct = row["platform"], row["job_id"], row["account"]
        if acct not in cfgs: cfgs[acct] = settings if acct == settings.get("account", "default") else config.settings(acct)
        s = cfgs[acct]
        info = jobs.setdefault(jid, dict(row=row, items=[], fresh=False))
        ad = registry[p](http, tokens, s, log=lambda m, p=p: log(f"  {m}"), sleep=sleep, now=now, account=acct)
        miss = ad.missing()
        if miss:
            new = ledger.fail(jid, p, "not set up: " + ", ".join(miss))
            info["items"].append((p, "setup", ", ".join(miss))); info["fresh"] |= new
            out.append((p, "not set up", ", ".join(miss))); log(f"{p}{label(acct)}: not set up yet ({', '.join(miss)})"); continue
        if not first and s.get("stagger_seconds"): sleep(s["stagger_seconds"])
        first = False
        ledger.start(jid, p)
        try:
            job = job_from(json.loads(row["spec"]), s)
            if p not in job.kind: raise RuntimeError(f"{p} was not routed for this video")
            r = ad.publish(job)
            ledger.done(jid, p, r.remote_id, r.url, r.note)
            info["items"].append((p, "published", (r.url or r.remote_id) + (f"\n    {r.note}" if r.note else ""))); info["fresh"] = True
            out.append((p, "published", r.url or r.remote_id)); log(f"{p}{label(acct)}: published {r.url or r.remote_id}" + (f"  [{r.note}]" if r.note else ""))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            new = ledger.fail(jid, p, err)
            tail = " Gave up after %d attempts; `publish run --requeue` tries again." % (row["attempts"] + 1) if row["attempts"] + 1 >= settings["retries"] else ""
            info["items"].append((p, "failed", err[:300] + tail)); info["fresh"] |= new
            out.append((p, "failed", str(e))); log(f"{p}{label(acct)}: FAILED {err[:300]}")
    for jid, info in jobs.items():
        if not info["fresh"]: continue                                            # nothing new to say
        row, items = info["row"], info["items"]
        spec = json.loads(row["spec"])
        kind = "failed" if any(i[1] == "failed" for i in items) else "setup" if any(i[1] == "setup" for i in items) else "published"
        mark = {"published": "✅", "failed": "⚠️", "setup": "🔑"}
        body = "\n".join(f"{mark[i[1]]} {i[0]}: {i[2]}" for i in items)
        cover = Path(spec["cover"]) if spec.get("cover") else None
        notify_fn(cfgs[row["account"]], notify.Event(kind, f"{'Posted' if kind == 'published' else 'Problem posting'}: {Path(row['video']).name}{label(row['account'])}",
                                                     body, row["account"], jid, cover=cover if cover and cover.exists() else None), http, log)
    return out
