"""python -m publish <command>   (run from the repo root)

  ship VIDEO         caption + edit, export, write the copy, then (per the review mode) wait for you, or post
  plan VIDEO         what would happen, without rendering anything
  review             posts waiting for your approval, with where to watch each one
  approve JOB        let a waiting post go out (--platforms x,tiktok for just some; --now skips a schedule or delay)
  reject JOB         cancel a waiting or scheduled post
  edit JOB PLATFORM  change the words of one post before it goes out
  run                collect Telegram taps, post whatever is queued and due, retry failures (cron / launchd friendly)
  watch [FOLDER]     ship every new video dropped in FOLDER (or each account's own watch folder), and keep running the queue
  status             recent posts and their links
  accounts           the accounts in settings.toml and whether each is ready
  auth NAME          log in: youtube | tiktok | x | meta (Instagram + Facebook); --account NAME for a named account
  notify-test        send a test notification to an account's channels
  doctor             check ffmpeg, the caption models and each platform's credentials

Every command that touches one account takes --account NAME (accounts are [accounts.NAME] tables in settings.toml).
"""
import argparse, dataclasses, json, shutil, sys, time
from pathlib import Path

from . import config, export, notify, review as reviewmod, ship as shipmod, writer
from .http import Http
from .ledger import Ledger
from .platforms import REGISTRY
from .tokens import TokenStore

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

def parse_platforms(v):
    if not v or v == "all": return None
    names = [x.strip().lower() for x in v.split(",") if x.strip()]
    bad = [n for n in names if n not in REGISTRY]
    if bad: raise SystemExit(f"unknown platform {', '.join(bad)}; choose from {', '.join(REGISTRY)}")
    return names

def opts_from(a):
    return shipmod.Opts(platforms=parse_platforms(a.platforms), captions=a.captions, style=a.style, edit=a.edit,
                        cut_silence=True if a.cut_silence else None, music=a.music, text_file=a.text, title=a.title,
                        description=a.description, short=a.short, hashtags=[h.lstrip("#") for h in (a.hashtags or "").split(",") if h.strip()],
                        link=a.link, privacy=a.privacy, account=a.account, review=a.review, publish_at=a.at, dry_run=getattr(a, "dry_run", False), force=getattr(a, "force", False))

def add_ship_args(p):
    p.add_argument("--platforms", help="comma list or 'all' (default: all that fit this video)")
    p.add_argument("--captions", choices=["auto", "done", "off"], help="auto: burn captions in; done: the video already has them; off: none")
    p.add_argument("--style", help="caption style: auto (default), pop, serif, type, whisper, poster, hype, clean")
    p.add_argument("--edit", choices=["none", "subtle", "punchy"], help="zoom/punch edits (default subtle)")
    p.add_argument("--cut-silence", action="store_true", help="trim pauses between words")
    p.add_argument("--music", help="music file, ducked under the voice")
    p.add_argument("--text", help="the passage: captions use its exact words and the post copy is written from it")
    p.add_argument("--title"); p.add_argument("--description"); p.add_argument("--short", help="the short post text (X, TikTok, Instagram)")
    p.add_argument("--hashtags", help="comma list, added to the settings.toml ones"); p.add_argument("--link")
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], help="anything but public only goes to YouTube and TikTok")
    p.add_argument("--account", help="which account to post as ([accounts.NAME] in settings.toml)")
    p.add_argument("--review", choices=["manual", "delay", "auto"], help="manual: wait for approval (default); delay: post after a delay unless rejected; auto: post now")
    p.add_argument("--at", help='post later, local time: "2026-10-14 18:00" (run `publish run` or `publish watch` to deliver)')

def cmd_ship(a):
    o = opts_from(a)
    shipmod.ship(a.video, o, run=not a.dry_run)
    if a.dry_run: print("\n(dry run: nothing was posted; the captioned video is in", config.WORK_DIR, ")")
    return 0

def cmd_plan(a):
    o = opts_from(a); o.captions = "off"
    side = writer.sidecar(a.video)
    s = shipmod.settings_for(o, side)
    m = export.probe(a.video)
    text = shipmod.studio.plain(Path(a.text).read_text(encoding="utf-8")) if a.text else ""
    if not text:                                           # a transcript from an earlier ship, if there is one
        cached = config.WORK_DIR / export.sha256(a.video)[:12] / "words.json"
        if cached.exists(): text = " ".join(w["word"] for w in json.loads(cached.read_text()))
    kinds, texts, skipped, privacy = shipmod.decide(m, o, side, s, text)
    print(f"{Path(a.video).name}: {m.width}x{m.height}, {m.duration:.1f}s, {'vertical' if m.vertical else 'landscape'}, {m.vcodec}/{m.acodec}, privacy {privacy}")
    for p, kind in kinds.items():
        t = texts[p]; head = t["title"] or (t["text"].splitlines() or [""])[0]
        print(f"  {p:<10} {kind:<6} {head[:90]!r}")
    for p, why in skipped.items(): print(f"  {p:<10} skipped: {why}")
    if not text and not (o.title or side.get("title")):
        print("\n(no transcript yet, so the copy is empty: `ship` writes it from the captions, or pass --title / --text)")
    return 0

def cycle(led, s):
    """One pass: collect Telegram taps, then post whatever is due."""
    try: reviewmod.poll(led)
    except Exception as e: print(f"telegram: could not check for replies ({type(e).__name__}: {str(e)[:120]})")
    shipmod.run_due(led, s)

def cmd_run(a):
    s = config.settings(); led = Ledger()
    if a.requeue: led.requeue()
    while True:
        cycle(led, s)
        if not a.every: return 0
        time.sleep(a.every)

def cmd_status(a):
    rows = Ledger().status(a.limit, a.account)
    if not rows: print("nothing queued yet"); return 0
    for r in rows:
        when = time.strftime("%m-%d %H:%M", time.localtime(r["publish_at"]))
        extra = r["url"] or r["note"] or (r["error"] or "")[:90]
        acct = "" if r["account"] == "default" else f"[{r['account']}] "
        print(f"#{r['job_id']:<4} {when}  {acct}{Path(r['video']).name[:26]:<26} {r['platform']:<10} {r['status']:<9} {extra}")
    return 0

def cmd_review(a):
    led = Ledger(); jobs = led.awaiting()
    if not jobs: print("nothing is waiting for review"); return 0
    for j in jobs:
        if a.account and j["account"] != a.account: continue
        print(reviewmod.summary(led, j["id"])); print(f"  -> python -m publish approve {j['id']}   or   reject {j['id']}\n")
    return 0

def cmd_approve(a):
    led = Ledger()
    n = reviewmod.approve(led, a.job, parse_platforms(a.platforms), a.now)
    print(f"approved {n} post(s) of job #{a.job}" if n else f"job #{a.job} has nothing waiting for approval")
    if n and a.run: shipmod.run_due(led, config.settings())
    return 0 if n else 1

def cmd_reject(a):
    n = reviewmod.reject(Ledger(), a.job, parse_platforms(a.platforms), a.reason)
    print(f"rejected {n} post(s) of job #{a.job}" if n else f"job #{a.job} has nothing left to reject")
    return 0 if n else 1

def cmd_edit(a):
    t = reviewmod.edit(Ledger(), a.job, a.platform, a.title, a.text, a.description)
    print(f"#{a.job} {a.platform}: title={t.get('title')!r} text={t.get('text')!r}")
    return 0

def cmd_accounts(a):
    tokens = TokenStore(); http = Http()
    for name in config.account_names():
        s = config.settings(name)
        print(f"{name}: review {s['review']['mode']}" + (f" ({s['review']['delay_minutes']} min)" if s["review"]["mode"] == "delay" else "") +
              f", notify {', '.join(s['notify']['channels']) or 'nobody'}, platforms {', '.join(s['platforms'])}" + (f", watch {s['watch']}" if s["watch"] else ""))
        for p in s["platforms"]:
            miss = REGISTRY[p](http, tokens, s, account=name).missing()
            print(f"   {p:<10} {'ready' if not miss else 'needs: ' + ', '.join(miss)}")
    return 0

def cmd_notify_test(a):
    s = config.settings(a.account); http = Http()
    chans = notify.channels(s, http)
    if not chans: print("no channels set: add notify.channels to settings.toml (see settings.example.toml)"); return 1
    for ch in chans:
        miss = ch.missing()
        if miss: print(f"{ch.name}: needs {', '.join(miss)}"); continue
        try: ch.send(notify.Event("test", f"publish test{' [' + a.account + ']' if a.account else ''}", "If you can read this, notifications work.", s["account"])); print(f"{ch.name}: sent")
        except Exception as e: print(f"{ch.name}: FAILED {type(e).__name__}: {str(e)[:200]}")
    return 0

def cmd_doctor(a):
    s = config.settings(a.account); tokens = TokenStore()
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool); print(f"{tool:<14} {'ok' if found else 'MISSING (brew install ffmpeg)'}"); ok &= bool(found)
    for mod, hint in (("faster_whisper", "pip install faster-whisper"), ("mlx_whisper", "pip install mlx-whisper (Apple silicon, optional)")):
        try: __import__(mod); print(f"{mod:<14} ok")
        except ImportError: print(f"{mod:<14} not installed ({hint})")
    print(f"settings       {config.HOME / 'settings.toml'} {'(found)' if (config.HOME / 'settings.toml').exists() else '(none: defaults)'}")
    http = Http()
    for name, cls in REGISTRY.items():
        miss = cls(http, tokens, s, account=s["account"]).missing()
        print(f"{name:<14} {'ready' if not miss else 'needs: ' + ', '.join(miss)}")
    for ch in notify.channels(s, http):
        miss = ch.missing(); print(f"notify {ch.name:<7} {'ready' if not miss else 'needs: ' + ', '.join(miss)}")
    print(f"review         {s['review']['mode']}")
    return 0 if ok else 1

def cmd_auth(a):
    from .platforms import tiktok, x, youtube
    t = TokenStore()
    acct = a.account or "default"
    if a.name == "meta": return auth_meta(a, t, acct)
    mod = dict(youtube=youtube, tiktok=tiktok, x=x)[a.name]
    miss = [v for v in REGISTRY[a.name].env_vars if not config.env(v)]
    if miss: raise SystemExit(f"set {', '.join(miss)} first (environment or {config.HOME / '.env'}); see publish/README.md")
    if acct != "default": print(f"Log in to the {a.name} account for '{acct}' in your browser first (the login applies to whoever is signed in).")
    t.put(config.token_key(a.name, acct), mod.login()); print(f"{a.name}: logged in as '{acct}', saved to {config.TOKENS_PATH}")
    return 0

def auth_meta(a, tokens, acct):
    from .platforms import meta
    app, secret = config.env("META_APP_ID"), config.env("META_APP_SECRET")
    if not (app and secret): raise SystemExit("set META_APP_ID and META_APP_SECRET first; see publish/README.md")
    user = a.user_token or input("Paste a user access token from the Graph API Explorer (with pages_show_list, pages_manage_posts, "
                                 "instagram_basic, instagram_content_publish, publish_video): ").strip()
    http, api = Http(), meta.base(config.settings())
    ll = http.get(f"{api}/oauth/access_token", params=dict(grant_type="fb_exchange_token", client_id=app, client_secret=secret, fb_exchange_token=user)).json()["access_token"]
    pages = http.get(f"{api}/me/accounts", params=dict(access_token=ll, fields="id,name,access_token")).json().get("data", [])
    if not pages: raise SystemExit("that token can't see any Facebook Pages")
    pick = [p for p in pages if a.page in (None, p["id"], p["name"])] if a.page else pages
    if len(pick) != 1: raise SystemExit("which page? use --page with one of: " + ", ".join(f"{p['name']} ({p['id']})" for p in pages))
    pg = pick[0]
    ig = http.get(f"{api}/{pg['id']}", params=dict(fields="instagram_business_account", access_token=pg["access_token"])).json()
    ig_id = (ig.get("instagram_business_account") or {}).get("id")
    tokens.put(config.token_key(meta.LOGIN, acct), dict(access_token=pg["access_token"], page_id=pg["id"], page_name=pg["name"], ig_user_id=ig_id))
    print(f"meta: saved Page '{pg['name']}' ({pg['id']}) for '{acct}'" + (f" and Instagram account {ig_id}" if ig_id else
          "; no Instagram professional account is linked to it, so Instagram will stay disabled"))
    return 0

def cmd_watch(a):
    o = opts_from(a)
    if a.folder: targets = [(Path(a.folder).expanduser(), a.account)]
    else:
        targets = [(Path(config.settings(n)["watch"]).expanduser(), n) for n in config.account_names() if config.settings(n)["watch"]]
        if not targets: raise SystemExit("give a FOLDER, or set `watch = \"~/ToPost/name\"` in an [accounts.NAME] table")
    s = config.settings(); led = Ledger()
    for folder, _ in targets: (folder / "_posted").mkdir(parents=True, exist_ok=True)
    print("watching " + ", ".join(f"{f}" + (f" as {n}" if n else "") for f, n in targets) + " (Ctrl-C to stop)")
    while True:
        for folder, acct in targets:
            to = dataclasses.replace(o, account=acct)
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() not in VIDEO_EXT or f.name.startswith(".") or f.stem.endswith(".video") or not f.is_file() \
                        or time.time() - f.stat().st_mtime < a.settle: continue       # (*.video.mp4 is render.py's silent intermediate)
                print(f"\nnew video: {f.name}" + (f" [{acct}]" if acct else ""))
                try:
                    shipmod.ship(f, to, ledger=led, run=False)
                    shutil.move(str(f), str(folder / "_posted" / f.name))
                    for side in f.parent.glob(f.stem + ".post.toml"): shutil.move(str(side), str(folder / "_posted" / side.name))
                except Exception as e:                                  # leave the file where it is and carry on with the rest
                    print(f"  could not process {f.name}: {type(e).__name__}: {e}")
                    (folder / "_failed").mkdir(exist_ok=True)
                    shutil.move(str(f), str(folder / "_failed" / f.name))
        cycle(led, s)
        time.sleep(a.interval)

def main(argv=None):
    ap = argparse.ArgumentParser(prog="publish", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ship"); p.add_argument("video"); add_ship_args(p)
    p.add_argument("--dry-run", action="store_true", help="do all the local work (captions, export) but post nothing")
    p.add_argument("--force", action="store_true", help="post again even if this video was already posted"); p.set_defaults(fn=cmd_ship)
    p = sub.add_parser("plan"); p.add_argument("video"); add_ship_args(p); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("run"); p.add_argument("--every", type=int, help="keep running, checking every N seconds")
    p.add_argument("--requeue", action="store_true", help="give failed posts a fresh set of attempts"); p.set_defaults(fn=cmd_run)
    p = sub.add_parser("watch"); p.add_argument("folder", nargs="?"); add_ship_args(p)
    p.add_argument("--interval", type=int, default=30); p.add_argument("--settle", type=int, default=10, help="seconds a file must be unchanged before it is picked up")
    p.set_defaults(fn=cmd_watch)
    p = sub.add_parser("status"); p.add_argument("--limit", type=int, default=30); p.add_argument("--account"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("review"); p.add_argument("--account"); p.set_defaults(fn=cmd_review)
    p = sub.add_parser("approve"); p.add_argument("job", type=int); p.add_argument("--platforms")
    p.add_argument("--now", action="store_true", help="skip a scheduled time or the review delay")
    p.add_argument("--run", action="store_true", help="post right away instead of waiting for the next `run`"); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("reject"); p.add_argument("job", type=int); p.add_argument("--platforms"); p.add_argument("--reason"); p.set_defaults(fn=cmd_reject)
    p = sub.add_parser("edit"); p.add_argument("job", type=int); p.add_argument("platform", choices=list(REGISTRY))
    p.add_argument("--title"); p.add_argument("--text", help="the caption / post text"); p.add_argument("--description"); p.set_defaults(fn=cmd_edit)
    p = sub.add_parser("accounts"); p.set_defaults(fn=cmd_accounts)
    p = sub.add_parser("notify-test"); p.add_argument("--account"); p.set_defaults(fn=cmd_notify_test)
    p = sub.add_parser("auth"); p.add_argument("name", choices=["youtube", "tiktok", "x", "meta"]); p.add_argument("--account")
    p.add_argument("--user-token"); p.add_argument("--page", help="Facebook Page name or id (meta)"); p.set_defaults(fn=cmd_auth)
    p = sub.add_parser("doctor"); p.add_argument("--account"); p.set_defaults(fn=cmd_doctor)
    a = ap.parse_args(argv)
    try:
        return a.fn(a) or 0
    except KeyboardInterrupt:
        return 130

if __name__ == "__main__":
    sys.exit(main())
