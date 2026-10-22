"""Instagram Reels through the Instagram Graph API (resumable upload, so no public video URL is needed).

Needs an Instagram professional account linked to a Facebook Page and a Meta app with instagram_content_publish.
Run `python -m publish auth meta` once to store the Page token and the Instagram account id.
"""
from ..http import MissingAuth
from ..models import Result
from . import meta
from .base import Platform

class Instagram(Platform):
    name = "instagram"
    login = ""                                   # credentials live under "meta"

    def missing(self):
        c = meta.creds(self.tokens, self.account)
        return [f"{k} ({meta.auth_hint(self.account)})" for k in ("access_token", "ig_user_id") if not c.get(k)]

    def publish(self, job):
        c = meta.creds(self.tokens, self.account)
        if not c.get("access_token") or not c.get("ig_user_id"): raise MissingAuth(f"instagram: run {meta.auth_hint(self.account)}")
        api, tok, uid = meta.base(self.settings), c["access_token"], c["ig_user_id"]
        caption = job.texts["instagram"]["text"]
        box = self.http.post(f"{api}/{uid}/media", data=dict(media_type="REELS", upload_type="resumable", caption=caption,
                                                               share_to_feed="true", access_token=tok)).json()
        cid = box["id"]
        meta.rupload(self.http, box["uri"], tok, job.video)
        self.log(f"instagram: uploaded container {cid}")
        def check():
            s = self.http.get(f"{api}/{cid}", params=dict(fields="status_code,status", access_token=tok)).json()
            if s.get("status_code") == "FINISHED": return s
            if s.get("status_code") in ("ERROR", "EXPIRED"): raise RuntimeError(f"instagram: {s.get('status') or s.get('status_code')}")
            return None
        self.wait_for(check, "Instagram to process the video", timeout=900, every=6)
        media_id = self.http.post(f"{api}/{uid}/media_publish", data=dict(creation_id=cid, access_token=tok)).json()["id"]
        try:
            link = self.http.get(f"{api}/{media_id}", params=dict(fields="permalink", access_token=tok)).json().get("permalink")
        except Exception:
            link = None
        return Result(media_id, link)
