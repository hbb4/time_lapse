"""The review layer: posts wait here until you approve them (or, in delay mode, until the clock runs out)."""
from . import config, notify
from .http import Http

def approve(ledger, job_id, platforms=None, now=False):
    """Approve waiting posts (manual review), or, for a job in delay mode, skip the wait. now=True also skips a scheduled time."""
    n = ledger.approve(job_id, platforms, now)
    return n or ledger.expedite(job_id)

def reject(ledger, job_id, platforms=None, reason=None):
    return ledger.reject(job_id, platforms, reason)

def edit(ledger, job_id, platform, title=None, text=None, description=None):
    """Change the words of one platform's post before it goes out. Returns the new copy."""
    import json
    job = ledger.job(job_id)
    if not job: raise SystemExit(f"no job #{job_id}")
    spec = json.loads(job["spec"])
    if platform not in spec["texts"]: raise SystemExit(f"job #{job_id} has no {platform} post (it has: {', '.join(spec['texts'])})")
    t = spec["texts"][platform]
    for k, v in (("title", title), ("text", text), ("description", description)):
        if v is not None: t[k] = v
    ledger.set_spec(job_id, spec)
    return t

def summary(ledger, job_id):
    """Plain-text description of a job: where the video is, and each platform's status and copy."""
    import json
    from pathlib import Path
    job = ledger.job(job_id)
    spec = json.loads(job["spec"])
    lines = [f"#{job_id} [{job['account']}] {Path(job['video']).name}", f"  preview: {spec['upload']}"]
    for p in ledger.posts(job_id):
        t = spec["texts"].get(p["platform"], {})
        head = ((t.get("title") or t.get("text") or "").splitlines() or [""])[0][:100]
        lines.append(f"  {p['platform']:<10} {p['status']:<9} {head}")
    return "\n".join(lines)

def telegram_handler(ledger, log=print):
    """The function Telegram.poll calls: checks the chat is the account's own, then approves, rejects or lists."""
    def handle(action, job_id, chat_id):
        if action == "queue":
            rows = ledger.awaiting()
            allowed = [j for j in rows if str(config.settings(j["account"])["notify"]["telegram"].get("chat_id") or config.env("TELEGRAM_CHAT_ID")) == str(chat_id)]
            return "\n\n".join(summary(ledger, j["id"]) for j in allowed) or "Nothing is waiting for review."
        job = ledger.job(job_id)
        if not job: return f"No job #{job_id}."
        s = config.settings(job["account"])
        if str(s["notify"]["telegram"].get("chat_id") or config.env("TELEGRAM_CHAT_ID") or "") != str(chat_id):
            log(f"telegram: ignored a reply from chat {chat_id} (not the chat for account '{job['account']}')")
            return "Not authorized for this account."
        if action == "approve":
            n = approve(ledger, job_id)
            return f"Approved #{job_id}." if n else f"#{job_id} has nothing waiting for approval."
        n = reject(ledger, job_id, reason="rejected from Telegram")
        return f"Rejected #{job_id}." if n else f"#{job_id} has nothing left to reject."
    return handle

def poll(ledger, http=None, log=print):
    """Collect Telegram taps for every account that uses Telegram. One bot serves all accounts. Returns updates handled."""
    http = http or Http()
    for acct in config.account_names():
        s = config.settings(acct)
        if "telegram" in s["notify"].get("channels", []):
            tg = notify.Telegram(s["notify"].get("telegram", {}), http, log)
            if tg.token:                                          # the token is shared, so one poll covers every account
                return tg.poll(ledger, telegram_handler(ledger, log))
    return 0
