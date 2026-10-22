"""X (Twitter) API v2: chunked media upload, then a post with the video attached.

Needs an X developer app with OAuth 2.0 user auth (scopes tweet.read tweet.write users.read media.write offline.access).
Posting through the API requires a paid X API plan; check yours before relying on this one.
"""
from .. import config, oauth
from ..models import Result
from .base import Platform, read_chunks

API = "https://api.x.com"
CHUNK = 4 * 2**20

class X(Platform):
    name = login = "x"
    env_vars = ("X_CLIENT_ID",)

    def refresh(self, tok):
        secret = config.env("X_CLIENT_SECRET")
        j = self.http.post(f"{API}/2/oauth2/token", data=dict(grant_type="refresh_token", refresh_token=tok["refresh_token"],
                           client_id=config.env("X_CLIENT_ID")), auth=(config.env("X_CLIENT_ID"), secret) if secret else None).json()
        return dict(access_token=j["access_token"], refresh_token=j.get("refresh_token", tok["refresh_token"]),
                    expires_at=self.now() + j.get("expires_in", 7200))

    def publish(self, job):
        h = {"Authorization": "Bearer " + self.tokens.fresh(self.key, self.refresh)}
        size = job.video.stat().st_size
        mid = self.http.post(f"{API}/2/media/upload/initialize", headers=h, json=dict(media_type="video/mp4", total_bytes=size,
                             media_category="tweet_video")).json()["data"]["id"]
        for i, (_, body) in enumerate(read_chunks(job.video, CHUNK)):
            self.http.post(f"{API}/2/media/upload/{mid}/append", headers=h, data={"segment_index": str(i)}, files={"media": ("chunk", body)})
        info = (self.http.post(f"{API}/2/media/upload/{mid}/finalize", headers=h).json().get("data") or {}).get("processing_info")
        if info:
            def check():
                d = (self.http.get(f"{API}/2/media/upload", headers=h, params=dict(command="STATUS", media_id=mid)).json().get("data") or {})
                pi = d.get("processing_info") or {}
                if pi.get("state") == "failed": raise RuntimeError(f"x: {pi.get('error', pi)}")
                return d if pi.get("state") in (None, "succeeded") else None
            self.wait_for(check, "X to process the video", timeout=600, every=max(2, info.get("check_after_secs", 3)))
        text = job.texts["x"]["text"]
        j = self.http.post(f"{API}/2/tweets", headers=h, json={"text": text, "media": {"media_ids": [mid]}}).json()
        tid = j["data"]["id"]
        return Result(tid, f"https://x.com/i/status/{tid}")

def login(say=print):
    secret = config.env("X_CLIENT_SECRET")
    return oauth.authorize("https://x.com/i/oauth2/authorize", f"{API}/2/oauth2/token", config.env("X_CLIENT_ID"),
                           ["tweet.read", "tweet.write", "users.read", "media.write", "offline.access"], client_secret=secret,
                           token_auth="basic", say=say)
