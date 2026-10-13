import time

class Platform:
    """One destination. publish(job) uploads and returns a Result, or raises (the ledger records the error and retries)."""
    name = ""
    login = ""                       # token-store key
    env_vars = ()                    # names of the environment variables this platform needs

    def __init__(self, http, tokens, settings, log=print, sleep=time.sleep, now=time.time):
        self.http, self.tokens, self.settings, self.log, self.sleep, self.now = http, tokens, settings, log, sleep, now

    def missing(self):
        """What is not set up yet: environment variables and the login. Empty = ready to post."""
        from .. import config
        out = [v for v in self.env_vars if not config.env(v)]
        if self.login and not self.tokens.get(self.login).get("access_token"): out.append(f"login (python -m publish auth {self.login})")
        return out

    def publish(self, job):
        raise NotImplementedError

    def wait_for(self, check, what, timeout=900, every=5):
        """Poll check() until it returns something other than None; give up with a clear error after `timeout` seconds."""
        end = self.now() + timeout
        while True:
            v = check()
            if v is not None: return v
            if self.now() > end: raise TimeoutError(f"{self.name}: timed out waiting for {what}")
            self.sleep(every)

def read_chunks(path, size):
    """(offset, bytes) pairs covering the file."""
    with open(path, "rb") as f:
        off = 0
        while True:
            b = f.read(size)
            if not b: return
            yield off, b
            off += len(b)
