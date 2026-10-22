import json, os, subprocess, tempfile, time, unittest
from pathlib import Path
from unittest import mock

from publish import config, export, notify, review, ship as shipmod, writer
from publish.ledger import Ledger
from publish.models import Result
from publish.platforms import meta
from publish.tokens import TokenStore
from tests.test_publish import Base, Fake, Resp, media

def new_ledger(t=None):
    t = t if t is not None else [1000.0]
    return Ledger(Path(":memory:"), now=lambda: t[0]), t

class Accounts(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "settings.toml").write_text('''
hashtags = ["base"]
[review]
mode = "delay"
[accounts.clouds]
hashtags = ["clouds"]
review = { mode = "manual" }
watch = "~/ToPost/clouds"
[accounts.clouds.notify]
channels = ["ntfy"]
[accounts.clouds.notify.ntfy]
topic = "clouds-topic"
[accounts.night]
platforms = ["x"]
''')
        p = mock.patch.object(config, "HOME", self.home); p.start(); self.addCleanup(p.stop)

    def test_account_tables_layer_over_the_defaults(self):
        d, c, n = config.settings(), config.settings("clouds"), config.settings("night")
        self.assertEqual((d["review"]["mode"], c["review"]["mode"]), ("delay", "manual"))
        self.assertEqual(c["hashtags"], ["clouds"]); self.assertEqual(d["hashtags"], ["base"])
        self.assertEqual(c["review"]["delay_minutes"], 30)                       # untouched keys survive the merge
        self.assertEqual(c["notify"]["ntfy"]["topic"], "clouds-topic"); self.assertEqual(d["notify"]["ntfy"]["topic"], "")
        self.assertEqual(n["platforms"], ["x"]); self.assertEqual(c["account"], "clouds")
        self.assertEqual(config.account_names(), ["default", "clouds", "night"])
        with self.assertRaises(SystemExit): config.settings("nope")

    def test_each_account_has_its_own_login(self):
        self.assertEqual(config.token_key("x", "clouds"), "x:clouds"); self.assertEqual(config.token_key("x"), "x")
        t = TokenStore(self.home / "t.json")
        t.put("x:clouds", dict(access_token="C", expires_at=time.time() + 999)); t.put("x", dict(access_token="D", expires_at=time.time() + 999))
        from publish.platforms import REGISTRY
        a = REGISTRY["x"](None, t, config.settings("clouds"), account="clouds"); d = REGISTRY["x"](None, t, config.settings())
        self.assertEqual((t.fresh(a.key), t.fresh(d.key)), ("C", "D"))
        self.assertIn("--account clouds", " ".join(REGISTRY["youtube"](None, t, config.settings("clouds"), account="clouds").missing()))
        t.put("meta:clouds", dict(access_token="P", page_id="PG1", ig_user_id="IG1"))
        self.assertEqual(meta.creds(t, "clouds")["page_id"], "PG1"); self.assertIsNone(meta.creds(t, "night")["page_id"])

class LedgerReview(unittest.TestCase):
    def test_review_lifecycle(self):
        L, t = new_ledger()
        j = L.enqueue("s", "v", {}, ["youtube", "x"], status="awaiting", account="clouds")
        self.assertEqual(L.due(3), []); self.assertEqual([r["id"] for r in L.awaiting()], [j])
        self.assertEqual(L.approve(j, ["x"]), 1)
        self.assertEqual([r["platform"] for r in L.due(3)], ["x"])                  # only the approved platform is released
        self.assertEqual(L.reject(j, ["youtube"], reason="meh"), 1)                 # the other one, still awaiting
        self.assertEqual({p["platform"]: p["status"] for p in L.posts(j)}, {"x": "queued", "youtube": "rejected"})
        self.assertEqual(L.reject(j), 1)                                            # no platform named: everything not started is cancelled
        L2, _ = new_ledger(); k = L2.enqueue("s", "v", {}, ["x"]); L2.start(k, "x"); L2.done(k, "x", "1", "u")
        self.assertEqual(L2.reject(k), 0)                                           # published posts can't be rejected

    def test_scheduled_time_is_kept_on_approval_unless_now(self):
        L, t = new_ledger()
        j = L.enqueue("s", "v", {}, ["x"], publish_at=5000.0, status="awaiting")
        L.approve(j); self.assertEqual(L.due(3), [])
        self.assertEqual(L.expedite(j), 1); self.assertEqual(len(L.due(3)), 1)

    def test_delay_mode_approve_means_post_now(self):
        L, t = new_ledger()
        j = L.enqueue("s", "v", {}, ["x"], publish_at=t[0] + 1800)
        self.assertEqual(L.due(3), []); self.assertEqual(review.approve(L, j), 1); self.assertEqual(len(L.due(3)), 1)
        self.assertEqual(review.reject(L, j), 1); self.assertEqual(L.due(3), [])

    def test_accounts_do_not_share_idempotency(self):
        L, _ = new_ledger()
        a = L.enqueue("s", "v", {}, ["x"], account="clouds"); L.start(a, "x"); L.done(a, "x", "1", "u")
        self.assertIsNotNone(L.published("s", "x", "clouds")); self.assertIsNone(L.published("s", "x", "night"))
        self.assertIsNone(L.pending("s", "x", 3, "night"))

    def test_fail_reports_whether_the_error_is_new(self):
        L, _ = new_ledger(); j = L.enqueue("s", "v", {}, ["x"])
        self.assertTrue(L.fail(j, "x", "boom")); self.assertFalse(L.fail(j, "x", "boom")); self.assertTrue(L.fail(j, "x", "different"))

    def test_migrates_a_ledger_from_before_accounts(self):
        d = Path(tempfile.mkdtemp()) / "old.db"
        import sqlite3
        c = sqlite3.connect(d); c.executescript("CREATE TABLE jobs (id INTEGER PRIMARY KEY, sha TEXT NOT NULL, video TEXT NOT NULL, created REAL NOT NULL, "
                                                "publish_at REAL NOT NULL, spec TEXT NOT NULL); INSERT INTO jobs VALUES (1,'s','v',0,0,'{}');"); c.commit(); c.close()
        L = Ledger(d); self.assertEqual(L.job(1)["account"], "default")

class Telegram(Base):
    def setUp(self):
        super().setUp()
        os.environ["TELEGRAM_BOT_TOKEN"] = "BOT"; self.addCleanup(os.environ.pop, "TELEGRAM_BOT_TOKEN", None)
        self.s["notify"] = dict(channels=["telegram"], telegram={"chat_id": "42"})

    def test_sends_the_video_with_buttons_and_falls_back(self):
        f = Fake([("POST", "sendVideo", Resp(200, {"ok": True})), ("POST", "sendPhoto", Resp(200, {"ok": True})), ("POST", "sendMessage", Resp(200, {"ok": True}))])
        ev = notify.Event("review", "Review #7", "body", job_id=7, video=self.video, cover=self.cover, actions=True)
        notify.Telegram({"chat_id": "42"}, f).send(ev)
        m, u, kw = f.calls[0]
        self.assertTrue(u.endswith("/botBOT/sendVideo")); self.assertEqual(kw["data"]["chat_id"], "42")
        self.assertEqual([b["callback_data"] for b in json.loads(kw["data"]["reply_markup"])["inline_keyboard"][0]], ["a:7", "r:7"])
        big = self.tmp / "big.mp4"; big.write_bytes(b"x")
        with mock.patch.object(notify, "TELEGRAM_VIDEO_LIMIT", 0):                           # too big for a bot: cover goes instead
            notify.Telegram({"chat_id": "42"}, f).send(ev)
        self.assertTrue(f.calls[1][1].endswith("sendPhoto"))
        notify.Telegram({"chat_id": "42"}, f).send(notify.Event("published", "done", "x"))
        self.assertTrue(f.calls[2][1].endswith("sendMessage")); self.assertNotIn("reply_markup", f.calls[2][2]["data"])

    def test_taps_approve_reject_and_strangers_are_ignored(self):
        L, _ = new_ledger()
        j1 = L.enqueue("s", "v1", {}, ["x"], status="awaiting"); j2 = L.enqueue("s2", "v2", {}, ["x"], status="awaiting")
        updates = [dict(update_id=10, callback_query=dict(id="c1", data=f"a:{j1}", message=dict(chat=dict(id=42), message_id=5))),
                   dict(update_id=11, callback_query=dict(id="c2", data=f"a:{j2}", message=dict(chat=dict(id=999), message_id=6))),     # not your chat
                   dict(update_id=12, message=dict(chat=dict(id=42), text=f"/reject {j2}"))]
        f = Fake([("POST", "getUpdates", Resp(200, {"result": updates})), ("POST", "answerCallbackQuery", Resp(200, {})),
                  ("POST", "editMessageReplyMarkup", Resp(200, {})), ("POST", "sendMessage", Resp(200, {}))])
        with mock.patch.object(config, "settings", return_value=self.s), mock.patch.object(config, "account_names", return_value=["default"]):
            n = notify.Telegram({"chat_id": "42"}, f).poll(L, review.telegram_handler(L, log=lambda m: None))
        self.assertEqual(n, 3)
        self.assertEqual({p["status"] for p in L.posts(j1)}, {"queued"})                      # approved by the right chat
        self.assertEqual({p["status"] for p in L.posts(j2)}, {"rejected"})                    # the stranger's approve did nothing; the typed reject worked
        self.assertEqual(L.get("telegram_offset"), "13")
        answers = [kw["data"]["text"] for m, u, kw in f.calls if u.endswith("answerCallbackQuery")]
        self.assertTrue(answers[0].startswith("Approved")); self.assertIn("Not authorized", answers[1])
        self.assertEqual(sum(u.endswith("editMessageReplyMarkup") for _, u, _ in f.calls), 1)   # buttons removed only for the real tap

class RunNotifications(Base):
    def test_one_summary_per_job_and_repeated_failures_stay_quiet(self):
        L, _ = new_ledger()
        spec = dict(upload=str(self.video), cover=None, srt=None, privacy="public", attended=True, kinds={"youtube": "short", "x": "post"},
                    texts={p: writer.build(p, k, self.s, "Hello there.") for p, k in {"youtube": "short", "x": "post"}.items()})
        L.enqueue("sha", self.video, spec, ["youtube", "x"], account="default")
        class Ok:
            def __init__(s, *a, **k): pass
            def missing(s): return []
            def publish(s, job): return Result("1", "https://yt/1", "private for now")
        class Bad(Ok):
            def publish(s, job): raise RuntimeError("X is down")
        events = []
        patch = mock.patch.object(shipmod.export, "probe", return_value=media()); patch.start(); self.addCleanup(patch.stop)
        run = lambda: shipmod.run_due(L, self.s, tokens=self.tokens, registry={"youtube": Ok, "x": Bad}, log=lambda m: None, sleep=lambda s: None,
                                      http=object(), notify_fn=lambda s, ev, http, log: events.append(ev))
        run()
        self.assertEqual(len(events), 1); ev = events[0]
        self.assertEqual(ev.kind, "failed"); self.assertIn("https://yt/1", ev.body); self.assertIn("X is down", ev.body); self.assertIn("private for now", ev.body)
        run(); run()                                                                           # same failure again: nothing new to say
        self.assertEqual(len(events), 1)

class ShipReviewModes(unittest.TestCase):
    """Real ffmpeg + a fake notifier: manual waits, delay schedules, auto posts; each notifies."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["TL_PUBLISH_STATE"] = str(self.tmp / "state"); self.addCleanup(os.environ.pop, "TL_PUBLISH_STATE", None)
        import importlib; importlib.reload(config)
        self.v = self.tmp / "clip.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=2", "-f", "lavfi", "-i", "sine=f=300:d=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(self.v)], check=True)
        work = config.WORK_DIR / export.sha256(self.v)[:12]; work.mkdir(parents=True)
        (work / "words.json").write_text(json.dumps([dict(word=w, start=0.2 + i * 0.3, end=0.45 + i * 0.3) for i, w in enumerate("Stop scrolling now friend.".split())]))
        self.events, self.posted = [], []
        class Ok:
            def __init__(s, *a, **k): pass
            def missing(s): return []
            def publish(s, job): return Result("9", "https://x.com/i/status/9")
        self.reg = {"x": Ok}
        self.notify = mock.patch.object(shipmod.notify, "notify", side_effect=lambda s, ev, http, log=print: self.events.append(ev) or 1); self.notify.start()
        self.addCleanup(self.notify.stop)

    def ship(self, review, account=None, **kw):
        o = shipmod.Opts(platforms=["x"], review=review, account=account, force=True, **kw)
        L = self.L
        return shipmod.ship(self.v, o, ledger=L, log=lambda m: None, registry=self.reg, tokens=object(), http=object(), sleep=lambda s: None)

    def test_modes(self):
        self.L = Ledger(Path(":memory:"))
        j = self.ship("manual")
        self.assertEqual([p["status"] for p in self.L.posts(j)], ["awaiting"])
        ev = self.events[-1]; self.assertEqual((ev.kind, ev.job_id, ev.actions), ("review", j, True)); self.assertTrue(ev.video.exists()); self.assertIn("approve", ev.body)
        shipmod.run_due(self.L, config.settings(), registry=self.reg, tokens=object(), http=object(), log=lambda m: None, notify_fn=lambda *a, **k: None)
        self.assertEqual([p["status"] for p in self.L.posts(j)], ["awaiting"])                  # nothing goes out unapproved
        review.approve(self.L, j)
        shipmod.run_due(self.L, config.settings(), registry=self.reg, tokens=object(), http=object(), log=lambda m: None, notify_fn=lambda s, ev, h, l: self.events.append(ev))
        self.assertEqual([p["status"] for p in self.L.posts(j)], ["published"]); self.assertEqual(self.events[-1].kind, "published")

        j = self.ship("delay")
        self.assertEqual([p["status"] for p in self.L.posts(j)], ["queued"]); self.assertGreater(self.L.job(j)["publish_at"], time.time() + 25 * 60)
        self.assertEqual(self.events[-1].kind, "queued")
        self.assertEqual(review.reject(self.L, j), 1)

        n = len(self.events); j = self.ship("auto")
        self.assertEqual([p["status"] for p in self.L.posts(j)], ["published"])
        self.assertEqual(len(self.events), n)                                                    # auto: no review notice (the publish notice comes from run_due's own notifier)

if __name__ == "__main__":
    unittest.main()
