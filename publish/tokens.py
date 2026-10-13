"""OAuth tokens on disk (0600), refreshed when they are about to expire."""
import json, os, time

from . import config
from .http import MissingAuth

class TokenStore:
    def __init__(self, path=None, now=time.time):
        self.path, self.now = path or config.TOKENS_PATH, now

    def _all(self):
        try: return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError): return {}

    def get(self, name): return self._all().get(name, {})

    def put(self, name, tok):
        data = self._all(); data[name] = tok
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f: json.dump(data, f, indent=1)
        os.replace(tmp, self.path)

    def fresh(self, name, refresh=None):
        """The access token for `name`, refreshed first if it expires within two minutes."""
        tok = self.get(name)
        if not tok.get("access_token"):
            raise MissingAuth(f"{name}: not logged in. Run: python -m publish auth {name}")
        if refresh and tok.get("expires_at") and tok["expires_at"] - 120 < self.now():
            if not tok.get("refresh_token"):
                raise MissingAuth(f"{name}: token expired and there is no refresh token. Run: python -m publish auth {name}")
            tok.update(refresh(tok)); self.put(name, tok)
        return tok["access_token"]
