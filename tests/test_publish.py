import json, os, subprocess, tempfile, time, unittest
from pathlib import Path
from unittest import mock

from publish import config, export, ship as shipmod, writer
from publish.http import ApiError, Http, MissingAuth
from publish.ledger import Ledger
from publish.models import Job, Media
from publish.platforms import REGISTRY
from publish.platforms import tiktok as tiktok_mod
from publish.tokens import TokenStore

S = config.settings()

def media(w=1080, h=1920, d=30.0, size=10 * 2**20, **kw):
    return Media(Path("x.mp4"), w, h, d, 30.0, kw.get("v", "h264"), kw.get("a", "aac"), "yuv420p", size)

class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._b, self.headers, self.text = status, body if body is not None else {}, headers or {}, json.dumps(body or {})
    def json(self): return self._b

class Fake:
    """Stands in for Http: records (method, url, kwargs) and answers with the first matching (method, url fragment) rule."""
    def __init__(self, rules): self.rules, self.calls = rules, []
    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for m, frag, resp in self.rules:
            if m == method and frag in url:
                r = resp(kw) if callable(resp) else resp
                return r
        raise AssertionError(f"unexpected {method} {url}")
    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, **kw): return self.request("POST", url, **kw)
    def put(self, url, **kw): return self.request("PUT", url, **kw)
    def urls(self): return [(m, u) for m, u, _ in self.calls]

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.video = self.tmp / "v.mp4"; self.video.write_bytes(os.urandom(9 * 2**20))
        self.cover = self.tmp / "c.jpg"; self.cover.write_bytes(b"jpg")
        self.srt = self.tmp / "s.srt"; self.srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        self.tokens = TokenStore(self.tmp / "tokens.json")
        for n in ("youtube", "tiktok", "x"): self.tokens.put(n, dict(access_token=n.upper(), expires_at=time.time() + 9999))
        self.tokens.put("meta", dict(access_token="PAGE", page_id="PG", ig_user_id="IG"))
        self.s = config.settings(); self.s["meta_api_version"] = "v23.0"
    def job(self, kinds, privacy="public", d=30.0):
        texts = {p: writer.build(p, k, self.s, "Hello world. This is a test post about long exposure.", link="") for p, k in kinds.items()}
        return Job(self.video, media(d=d, size=self.video.stat().st_size), texts, kinds, privacy, self.cover, self.srt, self.s)
    def ad(self, name, fake): return REGISTRY[name](fake, self.tokens, self.s, log=lambda m: None, sleep=lambda s: None)

class Writer(unittest.TestCase):
    def test_limits_hold_for_every_platform(self):
        text = " ".join(f"Sentence number {i} is quite long and wordy indeed." for i in range(300))
        for p, k in [("youtube", "short"), ("youtube", "long"), ("tiktok", "reel"), ("instagram", "reel"), ("facebook", "reel"), ("x", "post")]:
            t = writer.build(p, k, S, text, link="https://example.com/some/long/link", tags=["timelapse", "sf"])
            lim = S["limits"][p]
            if p == "youtube": self.assertLessEqual(len(t["title"]), 100); self.assertLessEqual(len(t["description"]), lim["description"])
            if p == "x": self.assertLessEqual(len(t["text"].replace("https://example.com/some/long/link", "x" * 23)), 280)
            if p in ("tiktok", "instagram"): self.assertLessEqual(len(t["text"]), 2200)
        self.assertTrue(writer.build("youtube", "short", S, "Hi there friend.")["title"].endswith("#Shorts"))

    def test_hashtag_caps_and_overrides(self):
        t = writer.build("x", "post", S, "A long exposure photography story about battery battery battery.", tags=["a", "b", "c", "d"])
        self.assertLessEqual(t["text"].count("#"), 2)
        t = writer.build("tiktok", "reel", S, "Whatever.", overrides={"text": "my own words"})
        self.assertTrue(t["text"].startswith("my own words"))

class Route(unittest.TestCase):
    def test_routing(self):
        r = export.route(media(), config.PLATFORMS, S)
        self.assertEqual(r["youtube"][0], "short"); self.assertEqual(r["instagram"][0], "reel"); self.assertEqual(r["facebook"][0], "reel")
        r = export.route(media(1920, 1080, 60), config.PLATFORMS, S)
        self.assertEqual(r["youtube"][0], "long"); self.assertIsNone(r["instagram"][0]); self.assertEqual(r["facebook"][0], "video")
        self.assertEqual(r["x"][0], "post")
        r = export.route(media(1920, 1080, 150), config.PLATFORMS, S)
        self.assertIsNone(r["x"][0]); self.assertIn("140", r["x"][1])                    # over X's cap
        self.assertIsNone(r["facebook"][0]); self.assertEqual(r["tiktok"][0], "post")    # over the 90 s default; TikTok allows it
        r = export.route(media(d=200), config.PLATFORMS, S)
        self.assertEqual(r["youtube"][0], "long")                                         # past 3 min is no longer a Short

class HttpRetry(unittest.TestCase):
    def test_retries_then_raises_readable_error(self):
        class Sess:
            n = 0
            def request(self, m, u, **kw):
                Sess.n += 1
                return Resp(503) if Sess.n < 3 else Resp(400, {"error": "bad"})
        sleeps = []
        h = Http(Sess(), retries=4, sleep=sleeps.append)
        with self.assertRaises(ApiError) as cm: h.get("https://x/y?access_token=SECRET")
        self.assertEqual(len(sleeps), 2); self.assertNotIn("SECRET", str(cm.exception)); self.assertIn("400", str(cm.exception))

class LedgerTests(unittest.TestCase):
    def test_idempotency_retry_and_schedule(self):
        t = [1000.0]; L = Ledger(Path(":memory:"), now=lambda: t[0])
        j = L.enqueue("sha", "v.mp4", {}, ["youtube", "x"], publish_at=2000.0, skipped={"instagram": "landscape"})
        self.assertEqual(L.due(3), [])                                      # scheduled for later
        t[0] = 2001.0
        self.assertEqual({r["platform"] for r in L.due(3)}, {"youtube", "x"})
        L.start(j, "youtube"); L.done(j, "youtube", "id1", "https://yt/1")
        self.assertEqual(L.published("sha", "youtube")["url"], "https://yt/1"); self.assertIsNone(L.published("sha", "x"))
        for _ in range(3): L.start(j, "x"); L.fail(j, "x", "boom")
        self.assertEqual(L.due(3), []); L.requeue()
        self.assertEqual([r["platform"] for r in L.due(3)], ["x"])

class Adapters(Base):
    def test_youtube_short(self):
        def init(kw):
            body = json.loads(kw["data"]); self.assertEqual(body["status"]["privacyStatus"], "public")
            self.assertTrue(body["snippet"]["title"].endswith("#Shorts"))
            return Resp(200, {}, {"Location": "https://up/session"})
        chunks = []
        def put(kw):
            chunks.append(kw["headers"]["Content-Range"])
            return Resp(308, {}, {"Range": "bytes=0-1"}) if len(chunks) < 3 else Resp(200, {"id": "VID", "status": {"privacyStatus": "public"}})
        f = Fake([("POST", "uploadType=resumable", init), ("PUT", "https://up/session", put)])
        r = self.ad("youtube", f).publish(self.job({"youtube": "short"}))
        self.assertEqual(r.url, "https://www.youtube.com/shorts/VID"); self.assertTrue(chunks[0].startswith("bytes 0-4194303/"))
        self.assertEqual(len([u for _, u in f.urls() if "thumbnails" in u or "captions" in u]), 0)     # shorts: no thumbnail/subtitles

    def test_youtube_long_adds_thumbnail_and_subtitles(self):
        f = Fake([("POST", "uploadType=resumable", Resp(200, {}, {"Location": "https://up/s"})),
                  ("PUT", "https://up/s", lambda kw: Resp(200, {"id": "V2", "status": {"privacyStatus": "private"}})),
                  ("POST", "thumbnails/set", Resp(200)), ("POST", "captions?", Resp(200))])
        r = self.ad("youtube", f).publish(self.job({"youtube": "long"}))
        self.assertTrue(r.url.endswith("watch?v=V2")); self.assertIn("audit", r.note)
        self.assertTrue(any("thumbnails/set?videoId=V2" in u for _, u in f.urls())); self.assertTrue(any("captions" in u for _, u in f.urls()))

    def test_tiktok_direct_forced_private(self):
        polls = [0]
        def status(kw):
            polls[0] += 1
            return Resp(200, {"data": {"status": "PROCESSING_UPLOAD" if polls[0] < 2 else "PUBLISH_COMPLETE", "publicaly_available_post_id": [123]}})
        def init(kw):
            self.assertEqual(kw["json"]["post_info"]["privacy_level"], "SELF_ONLY")
            self.assertEqual(kw["json"]["source_info"]["total_chunk_count"], 1)
            return Resp(200, {"data": {"publish_id": "P1", "upload_url": "https://tt/up"}})
        f = Fake([("POST", "creator_info", Resp(200, {"data": {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 600}})),
                  ("POST", "video/init", init), ("PUT", "https://tt/up", Resp(201)), ("POST", "status/fetch", status)])
        r = self.ad("tiktok", f).publish(self.job({"tiktok": "reel"}))
        self.assertEqual(r.remote_id, "123"); self.assertIn("only me", r.note)

    def test_tiktok_chunk_plan_and_duration_cap(self):
        self.assertEqual(tiktok_mod.chunk_plan(10 * 2**20), (10 * 2**20, 1))
        cs, n = tiktok_mod.chunk_plan(150 * 2**20)
        self.assertEqual((cs, n), (32 * 2**20, 4)); self.assertLess(150 * 2**20 - (n - 1) * cs, 128 * 2**20)
        f = Fake([("POST", "creator_info", Resp(200, {"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"], "max_video_post_duration_sec": 60}}))])
        with self.assertRaises(RuntimeError): self.ad("tiktok", f).publish(self.job({"tiktok": "reel"}, d=90))

    def test_instagram_reel(self):
        f = Fake([("POST", "/IG/media_publish", Resp(200, {"id": "M1"})), ("POST", "/IG/media", lambda kw: Resp(200, {"id": "C1", "uri": "https://ru/ig/C1"})),
                  ("POST", "https://ru/ig/C1", lambda kw: self.assertEqual(kw["headers"]["Authorization"], "OAuth PAGE") or Resp(200, {"success": True})),
                  ("GET", "/C1", Resp(200, {"status_code": "FINISHED"})), ("GET", "/M1", Resp(200, {"permalink": "https://instagram.com/reel/abc"}))])
        r = self.ad("instagram", f).publish(self.job({"instagram": "reel"}))
        self.assertEqual(r.url, "https://instagram.com/reel/abc")
        first = f.calls[0][2]["data"]; self.assertEqual((first["media_type"], first["upload_type"]), ("REELS", "resumable"))

    def test_instagram_surfaces_processing_error(self):
        f = Fake([("POST", "/IG/media", Resp(200, {"id": "C1", "uri": "https://ru/x"})), ("POST", "https://ru/x", Resp(200)),
                  ("GET", "/C1", Resp(200, {"status_code": "ERROR", "status": "bad aspect ratio"}))])
        with self.assertRaisesRegex(RuntimeError, "bad aspect"): self.ad("instagram", f).publish(self.job({"instagram": "reel"}))

    def test_facebook_reel_and_video(self):
        f = Fake([("POST", "/PG/video_reels", lambda kw: Resp(200, {"video_id": "R1", "upload_url": "https://ru/fb"}) if kw["data"]["upload_phase"] == "start" else Resp(200, {"success": True})),
                  ("POST", "https://ru/fb", Resp(200)), ("GET", "/R1", Resp(200, {"status": {"video_status": "ready"}}))])
        r = self.ad("facebook", f).publish(self.job({"facebook": "reel"}))
        self.assertEqual(r.url, "https://www.facebook.com/reel/R1")
        finish = [kw for m, u, kw in f.calls if m == "POST" and "video_reels" in u][-1]["data"]
        self.assertEqual((finish["upload_phase"], finish["video_state"]), ("finish", "PUBLISHED"))
        size = self.video.stat().st_size; step = 4 * 2**20; seen = []
        def vid(kw):
            ph = kw["data"]["upload_phase"]
            if ph == "start": return Resp(200, {"upload_session_id": "U", "video_id": "V", "start_offset": "0", "end_offset": str(step)})
            if ph == "transfer":
                s = int(kw["data"]["start_offset"]); seen.append(s); nxt = min(size, s + step)
                return Resp(200, {"start_offset": str(nxt), "end_offset": str(min(size, nxt + step))})
            return Resp(200, {"success": True})
        f = Fake([("POST", "/PG/videos", vid)])
        r = self.ad("facebook", f).publish(self.job({"facebook": "video"}))
        self.assertEqual(seen, [0, step, 2 * step]); self.assertIn("V", r.url)

    def test_x_chunked_upload_then_post(self):
        appended = []
        f = Fake([("POST", "media/upload/initialize", Resp(200, {"data": {"id": "M"}})),
                  ("POST", "/M/append", lambda kw: appended.append(kw["data"]["segment_index"]) or Resp(200)),
                  ("POST", "/M/finalize", Resp(200, {"data": {"processing_info": {"state": "pending", "check_after_secs": 1}}})),
                  ("GET", "media/upload", Resp(200, {"data": {"processing_info": {"state": "succeeded"}}})),
                  ("POST", "/2/tweets", lambda kw: self.assertEqual(kw["json"]["media"]["media_ids"], ["M"]) or Resp(201, {"data": {"id": "T1"}}))])
        r = self.ad("x", f).publish(self.job({"x": "post"}))
        self.assertEqual(appended, ["0", "1", "2"]); self.assertEqual(r.url, "https://x.com/i/status/T1")

    def test_token_refresh_and_missing_auth(self):
        self.tokens.put("x", dict(access_token="OLD", refresh_token="RT", expires_at=time.time() - 10))
        f = Fake([("POST", "oauth2/token", Resp(200, {"access_token": "NEW", "expires_in": 7200}))])
        os.environ["X_CLIENT_ID"] = "cid"
        try: self.assertEqual(self.tokens.fresh("x", self.ad("x", f).refresh), "NEW")
        finally: del os.environ["X_CLIENT_ID"]
        self.assertEqual(self.tokens.get("x")["refresh_token"], "RT")
        with self.assertRaises(MissingAuth): TokenStore(self.tmp / "none.json").fresh("youtube")
        self.assertEqual(oct(os.stat(self.tmp / "tokens.json").st_mode & 0o777), "0o600")

class Runner(Base):
    def test_one_failure_does_not_block_others_and_retries_work(self):
        L = Ledger(Path(":memory:"))
        spec = dict(upload=str(self.video), cover=None, srt=None, privacy="public",
                    kinds={"youtube": "short", "x": "post"}, texts={p: writer.build(p, k, self.s, "Hello there.") for p, k in {"youtube": "short", "x": "post"}.items()})
        L.enqueue("sha1", self.video, spec, ["youtube", "x", "tiktok"])
        calls = {"x": 0}
        class Boom:
            def __init__(s, *a, **k): pass
            def missing(s): return []
            def publish(s, job): calls["x"] += 1; raise RuntimeError("X is down") if calls["x"] == 1 else None
        class Ok(Boom):
            def publish(s, job): from publish.models import Result; return Result("1", "https://ok/1")
        class Unset(Boom):
            def missing(s): return ["TIKTOK_CLIENT_KEY"]
        reg = {"youtube": Ok, "x": Boom, "tiktok": Unset}
        patch = mock.patch.object(shipmod.export, "probe", return_value=media())
        patch.start(); self.addCleanup(patch.stop)
        shipmod.run_due(L, self.s, tokens=self.tokens, registry=reg, log=lambda m: None, sleep=lambda s: None, http=object())
        st = {r["platform"]: r["status"] for r in L.status()}
        self.assertEqual(st, {"tiktok": "failed", "x": "failed", "youtube": "published"})
        self.assertEqual(L.published("sha1", "youtube")["url"], "https://ok/1")
        class Fixed(Boom):
            def publish(s, job): from publish.models import Result; return Result("2", "https://ok/2")
        reg["x"] = Fixed
        shipmod.run_due(L, self.s, tokens=self.tokens, registry=reg, log=lambda m: None, sleep=lambda s: None, http=object())
        self.assertEqual(L.published("sha1", "x")["url"], "https://ok/2")
        self.assertEqual({r["platform"]: r["attempts"] for r in L.status()}["youtube"], 1)         # never posted twice

class Decide(Base):
    def test_private_never_leaks_to_platforms_without_a_private_mode(self):
        kinds, texts, skipped, privacy = shipmod.decide(media(), shipmod.Opts(privacy="private"), {}, self.s, "Hello there.")
        self.assertEqual(set(kinds), {"youtube", "tiktok"})
        self.assertEqual(set(skipped), {"instagram", "facebook", "x"}); self.assertEqual(privacy, "private")

    def test_sidecar_and_cli_layering(self):
        side = dict(privacy="unlisted", platforms=["youtube", "x"], hashtags=["side"], x={"text": "from the sidecar"})
        kinds, texts, _, privacy = shipmod.decide(media(), shipmod.Opts(hashtags=["cli"]), side, self.s, "Hello there friend.")
        self.assertEqual((set(kinds), privacy), ({"youtube"}, "unlisted"))                  # x has no private mode
        kinds, texts, _, _ = shipmod.decide(media(), shipmod.Opts(privacy="public", hashtags=["cli"]), side, self.s, "Hello there friend.")
        self.assertEqual(texts["x"]["text"].split(" #")[0], "from the sidecar")             # cli privacy beats the sidecar's
        self.assertIn("#cli", texts["youtube"]["description"]); self.assertIn("#side", texts["youtube"]["description"])

    def test_shipping_twice_does_not_queue_twice(self):
        L = Ledger(Path(":memory:"))
        L.enqueue("s", "v", {}, ["youtube"])
        self.assertIsNotNone(L.pending("s", "youtube", 3)); self.assertIsNone(L.pending("s", "x", 3))
        L.start(1, "youtube"); L.done(1, "youtube", "i", "u")
        self.assertIsNone(L.pending("s", "youtube", 3))

class EndToEnd(unittest.TestCase):
    """Real ffmpeg: a synthetic clip goes through caption -> export -> route -> copy (no network, dry run)."""
    def test_dry_run_ship(self):
        tmp = Path(tempfile.mkdtemp())
        os.environ["TL_PUBLISH_STATE"] = str(tmp / "state")
        self.addCleanup(os.environ.pop, "TL_PUBLISH_STATE", None)
        import importlib; importlib.reload(config)
        v = tmp / "clip.mov"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=3", "-f", "lavfi", "-i", "sine=f=300:d=3",
                        "-c:v", "libx264", "-pix_fmt", "yuv444p", "-c:a", "aac", str(v)], check=True)
        sha = export.sha256(v)
        work = config.WORK_DIR / sha[:12]; work.mkdir(parents=True)
        (work / "words.json").write_text(json.dumps([dict(word=w, start=0.2 + i * 0.4, end=0.5 + i * 0.4) for i, w in enumerate("Stop scrolling. This changes everything today.".split())]))
        (tmp / "clip.post.toml").write_text('hashtags = ["timelapse"]\n[x]\ntext = "my own x text"\n')
        out = []
        shipmod.ship(v, shipmod.Opts(platforms=["youtube", "instagram", "x"], dry_run=True), ledger=Ledger(Path(":memory:")), log=out.append)
        text = "\n".join(out)
        self.assertIn("1080x1920", text)                     # re-encoded to the vertical standard
        self.assertIn("youtube", text); self.assertIn("my own x text", text)
        up = next((work).glob("*.upload.mp4")); m = export.probe(up)
        self.assertEqual((m.vcodec, m.pix_fmt, m.acodec), ("h264", "yuv420p", "aac"))

if __name__ == "__main__":
    unittest.main()
