"""HTTP with retries and readable errors. Every platform call goes through Http so tests can swap the session."""
import re, time

import requests

RETRY = {408, 425, 429, 500, 502, 503, 504}

class ApiError(RuntimeError):
    def __init__(self, status, body, url, method="GET"):
        self.status, self.body = status, body
        super().__init__(f"{method} {mask(url)} -> {status}: {str(body)[:600]}")

class MissingAuth(RuntimeError):
    """A credential or login is missing; the message says how to get it."""

def mask(url):
    return re.sub(r"(access_token|client_secret|refresh_token)=[^&]+", r"\1=***", url)

class Http:
    def __init__(self, session=None, retries=4, sleep=time.sleep, timeout=120):
        self.session, self.retries, self.sleep, self.timeout = session or requests.Session(), retries, sleep, timeout

    def request(self, method, url, ok=range(200, 300), **kw):
        kw.setdefault("timeout", self.timeout)
        for attempt in range(self.retries + 1):
            try:
                r = self.session.request(method, url, **kw)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == self.retries: raise
                self.sleep(min(60, 2 ** (attempt + 1))); continue
            if r.status_code in ok: return r
            if r.status_code in RETRY and attempt < self.retries:
                wait = r.headers.get("Retry-After", "")
                self.sleep(min(120, float(wait)) if wait.replace(".", "", 1).isdigit() else min(60, 2 ** (attempt + 1)))
                continue
            try: body = r.json()
            except ValueError: body = r.text
            raise ApiError(r.status_code, body, url, method)

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, **kw): return self.request("POST", url, **kw)
    def put(self, url, **kw): return self.request("PUT", url, **kw)
