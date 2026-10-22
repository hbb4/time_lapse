"""Tell you what the publisher is doing, and (on Telegram) let you answer from your phone.

Events: review (rendered, waiting for you), queued (will post soon unless you reject it), published, failed, setup (a login or
credential is missing). Channels are chosen per account in settings.toml:

    [notify]
    channels = ["telegram", "macos"]
    [notify.telegram]
    chat_id = "123456789"

Telegram is two-way: the rendered video arrives with Approve / Reject buttons and `run` / `watch` act on your taps.
ntfy, Discord and macOS are one-way (approve with `python -m publish approve JOB`). A channel that fails never breaks posting.
"""
import json, re, subprocess, sys
from dataclasses import dataclass
from pathlib import Path

from . import config

TELEGRAM_VIDEO_LIMIT = 49 * 2**20          # bots can send files up to 50 MB

@dataclass
class Event:
    kind: str                              # review | queued | published | failed | setup | test
    title: str
    body: str = ""
    account: str = "default"
    job_id: int | None = None
    video: Path | None = None              # a file to show (a small preview is best)
    cover: Path | None = None
    actions: bool = False                  # offer Approve / Reject (Telegram buttons)

ICON = {"review": "🎬", "queued": "⏳", "published": "✅", "failed": "⚠️", "setup": "🔑", "test": "🔔"}

class Notifier:
    name = ""
    def __init__(self, cfg, http, log=print): self.cfg, self.http, self.log = cfg, http, log
    def configured(self): return True
    def missing(self): return []
    def send(self, ev): raise NotImplementedError

class MacOS(Notifier):
    name = "macos"
    def configured(self): return sys.platform == "darwin"
    def missing(self): return [] if sys.platform == "darwin" else ["only works on a Mac"]
    def send(self, ev):
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{esc(ev.body[:240])}" with title "{esc(ev.title)}"'], check=False, capture_output=True)

class Ntfy(Notifier):
    name = "ntfy"
    def configured(self): return bool(self.cfg.get("topic"))
    def missing(self): return [] if self.cfg.get("topic") else ["notify.ntfy.topic"]
    def send(self, ev):
        headers = {"Authorization": "Bearer " + config.env("NTFY_TOKEN")} if config.env("NTFY_TOKEN") else {}
        msg = ev.body + (f"\n\nApprove: python -m publish approve {ev.job_id}" if ev.actions and ev.job_id else "")
        self.http.post(self.cfg.get("server", "https://ntfy.sh").rstrip("/"), headers=headers,
                       json={"topic": self.cfg["topic"], "title": ev.title, "message": msg[:3500], "priority": 4 if ev.kind in ("failed", "review") else 3,
                             "tags": {"published": ["white_check_mark"], "failed": ["warning"], "review": ["movie_camera"]}.get(ev.kind, [])})

class Discord(Notifier):
    name = "discord"
    def configured(self): return bool(config.env("DISCORD_WEBHOOK_URL"))
    def missing(self): return [] if config.env("DISCORD_WEBHOOK_URL") else ["DISCORD_WEBHOOK_URL"]
    def send(self, ev):
        content = f"**{ev.title}**\n{ev.body}"[:1900] + (f"\nApprove: `python -m publish approve {ev.job_id}`" if ev.actions and ev.job_id else "")
        files = {"file": ("cover.jpg", ev.cover.read_bytes(), "image/jpeg")} if ev.cover and ev.cover.exists() else None
        self.http.post(config.env("DISCORD_WEBHOOK_URL"), data={"payload_json": json.dumps({"content": content})}, files=files)

class Telegram(Notifier):
    name = "telegram"
    def __init__(self, cfg, http, log=print):
        super().__init__(cfg, http, log)
        self.token, self.chat = config.env("TELEGRAM_BOT_TOKEN"), str(cfg.get("chat_id") or config.env("TELEGRAM_CHAT_ID") or "")
    def configured(self): return bool(self.token and self.chat)
    def missing(self): return [x for x, ok in (("TELEGRAM_BOT_TOKEN", self.token), ("notify.telegram.chat_id", self.chat)) if not ok]
    def api(self, method, **kw): return self.http.post(f"https://api.telegram.org/bot{self.token}/{method}", **kw).json()

    def send(self, ev):
        text = f"{ev.title}\n\n{ev.body}"
        markup = json.dumps({"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"a:{ev.job_id}"},
                                                  {"text": "❌ Reject", "callback_data": f"r:{ev.job_id}"}]]}) if ev.actions and ev.job_id else None
        data = {"chat_id": self.chat, "caption": text[:1000]}
        if markup: data["reply_markup"] = markup
        v, c = ev.video, ev.cover
        if v and v.exists() and v.stat().st_size <= TELEGRAM_VIDEO_LIMIT:
            self.api("sendVideo", data=dict(data, supports_streaming="true"), files={"video": (v.name, v.read_bytes(), "video/mp4")})
        elif c and c.exists():
            self.api("sendPhoto", data=data, files={"photo": (c.name, c.read_bytes(), "image/jpeg")})
        else:
            data = {"chat_id": self.chat, "text": text[:4000]}
            if markup: data["reply_markup"] = markup
            self.api("sendMessage", data=data)

    def poll(self, ledger, handler):
        """Act on taps and typed commands received since the last poll. handler(action, job_id, chat_id) -> reply text,
        where action is approve | reject | queue. Returns how many updates were handled."""
        offset = int(ledger.get("telegram_offset", 0) or 0)
        res = self.api("getUpdates", data={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["callback_query", "message"])})
        n = 0
        for u in res.get("result", []):
            offset = max(offset, u["update_id"] + 1); n += 1
            cb, msg = u.get("callback_query"), u.get("message")
            if cb:
                m = re.match(r"^([ar]):(\d+)$", cb.get("data", ""))
                chat = (cb.get("message") or {}).get("chat", {}).get("id")
                if not m: continue
                reply = handler("approve" if m.group(1) == "a" else "reject", int(m.group(2)), chat)
                self.api("answerCallbackQuery", data={"callback_query_id": cb["id"], "text": reply[:190]})
                if reply.startswith(("Approved", "Rejected")):                       # remove the buttons so it can't be tapped twice
                    self.api("editMessageReplyMarkup", data={"chat_id": chat, "message_id": cb["message"]["message_id"], "reply_markup": json.dumps({"inline_keyboard": []})})
            elif msg and msg.get("text"):
                chat = msg["chat"]["id"]
                m = re.match(r"^/?(approve|reject)\s+#?(\d+)\b", msg["text"].strip(), re.I)
                if m: reply = handler(m.group(1).lower(), int(m.group(2)), chat)
                elif re.match(r"^/?(queue|review|status)\b", msg["text"].strip(), re.I): reply = handler("queue", None, chat)
                else: continue
                self.api("sendMessage", data={"chat_id": chat, "text": reply[:4000]})
        ledger.put("telegram_offset", offset)
        return n

CHANNELS = {c.name: c for c in (Telegram, Ntfy, Discord, MacOS)}

def channels(settings, http, log=print):
    cfg = settings["notify"]
    out = []
    for name in cfg.get("channels", []):
        if name not in CHANNELS: log(f"notify: unknown channel '{name}' (choose from {', '.join(CHANNELS)})"); continue
        out.append(CHANNELS[name](cfg.get(name, {}), http, log))
    return out

def notify(settings, ev, http, log=print):
    """Send to every configured channel for the event's account; a channel that errors is logged, never raised."""
    sent = 0
    for ch in channels(settings, http, log):
        if not ch.configured(): continue
        try: ch.send(ev); sent += 1
        except Exception as e: log(f"notify: {ch.name} failed: {type(e).__name__}: {str(e)[:200]}")
    return sent
