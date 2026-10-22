"""TikTok Content Posting API: direct post (or draft to your inbox) with chunked upload.

Needs a TikTok developer app with the Content Posting API product. Until TikTok audits the app, every
direct post is forced to "only me" visibility; the result note says so. Drafts (tiktok_mode = "draft")
avoid that: the video lands in your TikTok inbox and you tap Post.
"""
from .. import config, oauth
from ..models import Result
from .base import Platform

API = "https://open.tiktokapis.com"
MB = 2**20

def chunk_plan(size):
    """(chunk_size, count) the way TikTok wants it: one chunk up to 64 MB, else 32 MB chunks with the remainder in the last."""
    if size <= 64 * MB: return size, 1
    return 32 * MB, size // (32 * MB)

class TikTok(Platform):
    name = login = "tiktok"
    env_vars = ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")

    def refresh(self, tok):
        j = self.http.post(f"{API}/v2/oauth/token/", data=dict(client_key=config.env("TIKTOK_CLIENT_KEY"), client_secret=config.env("TIKTOK_CLIENT_SECRET"),
                           grant_type="refresh_token", refresh_token=tok["refresh_token"])).json()
        out = dict(access_token=j["access_token"], expires_at=self.now() + j.get("expires_in", 86400))
        if j.get("refresh_token"): out["refresh_token"] = j["refresh_token"]
        return out

    def publish(self, job):
        t = job.texts["tiktok"]
        h = {"Authorization": "Bearer " + self.tokens.fresh(self.key, self.refresh), "Content-Type": "application/json; charset=UTF-8"}
        size = job.video.stat().st_size
        # TikTok wants a person to confirm each direct post, so a post nobody approved (review = auto/delay) is only ever a draft
        draft = self.settings["tiktok_mode"] == "draft" or (not job.attended and not self.settings["tiktok_direct_unattended"])
        chunk, count = chunk_plan(size)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        note = None
        if draft:
            init = self.http.post(f"{API}/v2/post/publish/inbox/video/init/", headers=h, json={"source_info": source}).json()
        else:
            info = self.http.post(f"{API}/v2/post/publish/creator_info/query/", headers=h, json={}).json()["data"]
            cap = info.get("max_video_post_duration_sec")
            if cap and job.media.duration > cap:
                raise RuntimeError(f"tiktok: this account can post {cap}s videos, this one is {job.media.duration:.0f}s")
            options = info.get("privacy_level_options", [])
            want = "PUBLIC_TO_EVERYONE" if job.privacy == "public" else "SELF_ONLY"
            level = want if want in options else ("SELF_ONLY" if "SELF_ONLY" in options else (options or ["SELF_ONLY"])[0])
            if level != want and want == "PUBLIC_TO_EVERYONE":
                note = "TikTok only allowed 'only me' for this account (the app has not been audited yet)"
            post = {"title": t["text"], "privacy_level": level, "disable_comment": False, "disable_duet": False, "disable_stitch": False,
                    "video_cover_timestamp_ms": 1000}
            init = self.http.post(f"{API}/v2/post/publish/video/init/", headers=h, json={"post_info": post, "source_info": source}).json()
        data = init["data"]; pid, url = data["publish_id"], data["upload_url"]
        for i in range(count):
            lo = i * chunk
            hi = size - 1 if i == count - 1 else lo + chunk - 1
            with open(job.video, "rb") as f:
                f.seek(lo); body = f.read(hi - lo + 1)
            self.http.put(url, data=body, ok=(200, 201, 206), headers={"Content-Type": "video/mp4", "Content-Length": str(len(body)),
                                                                        "Content-Range": f"bytes {lo}-{hi}/{size}"})
        self.log(f"tiktok: uploaded, publish_id {pid}")
        def check():
            d = self.http.post(f"{API}/v2/post/publish/status/fetch/", headers=h, json={"publish_id": pid}).json()["data"]
            st = d.get("status")
            if st == "FAILED": raise RuntimeError(f"tiktok: {d.get('fail_reason', 'failed')}")
            if st == "PUBLISH_COMPLETE": return d
            if st == "SEND_TO_USER_INBOX": return d
            return None
        d = self.wait_for(check, "TikTok to finish processing", timeout=900, every=6)
        ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
        if draft: note = "saved to your TikTok inbox (open the app, add sound if you like, and post). Caption to paste: " + t["text"]
        return Result(str(ids[0]) if ids else pid, None, note)

def login(say=print):
    redirect = config.env("TIKTOK_REDIRECT_URI", "http://localhost:8765/callback")
    return oauth.authorize("https://www.tiktok.com/v2/auth/authorize/", f"{API}/v2/oauth/token/", config.env("TIKTOK_CLIENT_KEY"),
                           ["user.info.basic", "video.publish", "video.upload"], client_secret=config.env("TIKTOK_CLIENT_SECRET"),
                           redirect_uri=redirect, id_param="client_key", scope_sep=",", use_pkce=False, say=say)
