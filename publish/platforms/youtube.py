"""YouTube Data API v3: resumable upload (Shorts and long form), thumbnail, and subtitles for long form.

Needs a Google Cloud project with the YouTube Data API enabled and an OAuth client (type: Desktop app).
Notes: videos.insert costs 1600 quota units (default 10,000/day, so about 6 uploads a day); and until the
project passes Google's API audit, uploads are forced private.
"""
import json

from .. import config, oauth
from ..models import Result
from .base import Platform, read_chunks

API = "https://www.googleapis.com"
CHUNK = 16 * 256 * 1024          # must be a multiple of 256 KiB

class YouTube(Platform):
    name = login = "youtube"
    env_vars = ("YT_CLIENT_ID", "YT_CLIENT_SECRET")

    def refresh(self, tok):
        r = self.http.post("https://oauth2.googleapis.com/token", data=dict(
            client_id=config.env("YT_CLIENT_ID"), client_secret=config.env("YT_CLIENT_SECRET"),
            refresh_token=tok["refresh_token"], grant_type="refresh_token"))
        j = r.json()
        return dict(access_token=j["access_token"], expires_at=self.now() + j.get("expires_in", 3600))

    def publish(self, job):
        t = job.texts["youtube"]; kind = job.kind["youtube"]
        auth = {"Authorization": "Bearer " + self.tokens.fresh(self.key, self.refresh)}
        size = job.video.stat().st_size
        meta = {"snippet": {"title": t["title"], "description": t["description"], "tags": t["tags"],
                            "categoryId": self.settings["youtube_category"]},
                "status": {"privacyStatus": job.privacy, "selfDeclaredMadeForKids": bool(self.settings["youtube_made_for_kids"])}}
        r = self.http.post(f"{API}/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                           headers=dict(auth, **{"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(size),
                                                 "X-Upload-Content-Type": "video/mp4"}), data=json.dumps(meta))
        url = r.headers["Location"]
        video = self._upload(url, job.video, size)
        vid = video["id"]
        self.log(f"youtube: uploaded {vid}")
        note = None
        if job.cover and kind == "long":
            try:
                self.http.post(f"{API}/upload/youtube/v3/thumbnails/set?videoId={vid}", headers=dict(auth, **{"Content-Type": "image/jpeg"}),
                               data=job.cover.read_bytes())
            except Exception as e:                      # needs a verified channel; the video is up regardless
                self.log(f"youtube: thumbnail not set ({e})")
        if job.captions and kind == "long":
            try: self._captions(vid, job.captions, auth)
            except Exception as e: self.log(f"youtube: subtitles not added ({e})")
        if job.privacy == "public" and (video.get("status") or {}).get("privacyStatus") == "private":
            note = "YouTube made this private: the API project has not passed Google's audit yet"
        link = f"https://www.youtube.com/shorts/{vid}" if kind == "short" else f"https://www.youtube.com/watch?v={vid}"
        return Result(vid, link, note)

    def _upload(self, url, path, size):
        for off, data in read_chunks(path, CHUNK):
            end = off + len(data) - 1
            r = self.http.put(url, data=data, ok=(200, 201, 308), allow_redirects=False,
                              headers={"Content-Length": str(len(data)), "Content-Range": f"bytes {off}-{end}/{size}"})
            if r.status_code in (200, 201): return r.json()
        raise RuntimeError("youtube: upload ended without a final response")

    def _captions(self, vid, srt, auth):
        boundary = "tlboundary"
        meta = json.dumps({"snippet": {"videoId": vid, "language": "en", "name": "English", "isDraft": False}})
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n--{boundary}\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + srt.read_bytes() + f"\r\n--{boundary}--".encode()
        self.http.post(f"{API}/upload/youtube/v3/captions?uploadType=multipart&part=snippet",
                       headers=dict(auth, **{"Content-Type": f"multipart/related; boundary={boundary}"}), data=body)

def login(say=print):
    tok = oauth.authorize("https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
                          config.env("YT_CLIENT_ID"), ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"],
                          client_secret=config.env("YT_CLIENT_SECRET"), extra={"access_type": "offline", "prompt": "consent"}, say=say)
    return tok
