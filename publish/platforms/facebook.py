"""Facebook Page posting: Reels (vertical, short) and regular videos (everything else), both resumable.

Uses the same Page token as Instagram (`python -m publish auth meta`); needs pages_manage_posts and publish_video.
"""
from ..http import MissingAuth
from ..models import Result
from . import meta
from .base import Platform

CHUNK = 8 * 2**20

class Facebook(Platform):
    name = "facebook"
    login = ""

    def missing(self):
        c = meta.creds(self.tokens, self.account)
        return [f"{k} ({meta.auth_hint(self.account)})" for k in ("access_token", "page_id") if not c.get(k)]

    def publish(self, job):
        c = meta.creds(self.tokens, self.account)
        if not c.get("access_token") or not c.get("page_id"): raise MissingAuth(f"facebook: run {meta.auth_hint(self.account)}")
        return (self._reel if job.kind["facebook"] == "reel" else self._video)(job, c)

    def _reel(self, job, c):
        api, tok, page = meta.base(self.settings), c["access_token"], c["page_id"]
        t = job.texts["facebook"]
        s = self.http.post(f"{api}/{page}/video_reels", data=dict(upload_phase="start", access_token=tok)).json()
        vid = s["video_id"]
        meta.rupload(self.http, s["upload_url"], tok, job.video)
        self.http.post(f"{api}/{page}/video_reels", data=dict(upload_phase="finish", video_id=vid, video_state="PUBLISHED",
                                                              description=t["text"], access_token=tok))
        self.log(f"facebook: reel {vid} sent")
        def check():
            st = self.http.get(f"{api}/{vid}", params=dict(fields="status", access_token=tok)).json().get("status", {})
            v = st.get("video_status")
            if v == "error": raise RuntimeError(f"facebook: {st}")
            return st if v in ("ready", "published") else None
        self.wait_for(check, "Facebook to process the reel", timeout=900, every=6)
        return Result(vid, f"https://www.facebook.com/reel/{vid}")

    def _video(self, job, c):
        api, tok, page = meta.base(self.settings), c["access_token"], c["page_id"]
        t = job.texts["facebook"]
        size = job.video.stat().st_size
        s = self.http.post(f"{api}/{page}/videos", data=dict(upload_phase="start", file_size=size, access_token=tok)).json()
        sid, vid = s["upload_session_id"], s["video_id"]
        start, end = int(s["start_offset"]), int(s["end_offset"])
        with open(job.video, "rb") as f:
            while start < size:
                f.seek(start); body = f.read(end - start)
                r = self.http.post(f"{api}/{page}/videos", data=dict(upload_phase="transfer", upload_session_id=sid, start_offset=start,
                                                                      access_token=tok), files={"video_file_chunk": ("chunk", body)}).json()
                start, end = int(r["start_offset"]), int(r["end_offset"])
        self.http.post(f"{api}/{page}/videos", data=dict(upload_phase="finish", upload_session_id=sid, title=t["title"],
                                                         description=t["text"], access_token=tok))
        return Result(vid, f"https://www.facebook.com/watch/?v={vid}")
