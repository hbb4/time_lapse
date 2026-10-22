"""SQLite record of what was queued, reviewed and posted, so a post is never made twice and failures can be retried."""
import json, sqlite3, time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, sha TEXT NOT NULL, video TEXT NOT NULL, created REAL NOT NULL,
  publish_at REAL NOT NULL, spec TEXT NOT NULL, account TEXT NOT NULL DEFAULT 'default');
CREATE TABLE IF NOT EXISTS posts (
  job_id INTEGER NOT NULL REFERENCES jobs(id), platform TEXT NOT NULL,
  status TEXT NOT NULL,            -- awaiting (needs your approval) | queued | running | published | failed | rejected | skipped
  attempts INTEGER NOT NULL DEFAULT 0, remote_id TEXT, url TEXT, note TEXT, error TEXT, updated REAL NOT NULL,
  PRIMARY KEY (job_id, platform));
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""
LIVE = "('queued', 'running', 'awaiting')"

class Ledger:
    def __init__(self, path=None, now=time.time):
        path = path or config.DB_PATH
        if str(path) != ":memory:": path.parent.mkdir(parents=True, exist_ok=True)
        self.db, self.now = sqlite3.connect(str(path)), now
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if "account" not in {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}:     # a ledger from before accounts existed
            self.db.execute("ALTER TABLE jobs ADD COLUMN account TEXT NOT NULL DEFAULT 'default'"); self.db.commit()

    def published(self, sha, platform, account="default"):
        """The existing published post of this video on this platform and account, if any."""
        return self.db.execute("SELECT p.* FROM posts p JOIN jobs j ON j.id = p.job_id "
                               "WHERE j.sha = ? AND j.account = ? AND p.platform = ? AND p.status = 'published'", (sha, account, platform)).fetchone()

    def pending(self, sha, platform, max_attempts, account="default"):
        """An awaiting, queued, running or retryable post of this video on this platform and account, if any."""
        return self.db.execute(f"SELECT p.* FROM posts p JOIN jobs j ON j.id = p.job_id WHERE j.sha = ? AND j.account = ? AND p.platform = ? AND "
                               f"(p.status IN {LIVE} OR (p.status = 'failed' AND p.attempts < ?))", (sha, account, platform, max_attempts)).fetchone()

    def enqueue(self, sha, video, spec, platforms, publish_at=None, skipped=None, account="default", status="queued"):
        """One job with a post row per platform; `skipped` = {platform: reason} for ones routed away."""
        now = self.now()
        cur = self.db.execute("INSERT INTO jobs (sha, video, created, publish_at, spec, account) VALUES (?,?,?,?,?,?)",
                              (sha, str(video), now, publish_at or now, json.dumps(spec), account))
        jid = cur.lastrowid
        for p in platforms:
            self.db.execute("INSERT INTO posts (job_id, platform, status, updated) VALUES (?,?,?,?)", (jid, p, status, now))
        for p, why in (skipped or {}).items():
            self.db.execute("INSERT INTO posts (job_id, platform, status, note, updated) VALUES (?,?,?,?,?)", (jid, p, "skipped", why, now))
        self.db.commit()
        return jid

    def job(self, job_id):
        return self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def posts(self, job_id):
        return self.db.execute("SELECT * FROM posts WHERE job_id = ? ORDER BY platform", (job_id,)).fetchall()

    def due(self, max_attempts):
        """Posts ready to run: queued and due, or failed with attempts left (oldest first). Awaiting posts never are."""
        return self.db.execute(
            "SELECT j.id AS job_id, j.sha, j.video, j.spec, j.publish_at, j.account, p.platform, p.attempts FROM posts p "
            "JOIN jobs j ON j.id = p.job_id WHERE j.publish_at <= ? AND "
            "(p.status = 'queued' OR (p.status = 'failed' AND p.attempts < ?)) ORDER BY j.publish_at, j.id", (self.now(), max_attempts)).fetchall()

    def start(self, job_id, platform):
        self.db.execute("UPDATE posts SET status='running', attempts=attempts+1, updated=? WHERE job_id=? AND platform=?",
                        (self.now(), job_id, platform)); self.db.commit()

    def done(self, job_id, platform, remote_id, url, note=None):
        self.db.execute("UPDATE posts SET status='published', remote_id=?, url=?, note=?, error=NULL, updated=? WHERE job_id=? AND platform=?",
                        (remote_id, url, note, self.now(), job_id, platform)); self.db.commit()

    def fail(self, job_id, platform, error):
        """Record a failure. Returns True when the error is new (so a notification is worth sending), False when it repeats."""
        old = self.db.execute("SELECT error FROM posts WHERE job_id=? AND platform=?", (job_id, platform)).fetchone()
        err = str(error)[:1500]
        self.db.execute("UPDATE posts SET status='failed', error=?, updated=? WHERE job_id=? AND platform=?", (err, self.now(), job_id, platform))
        self.db.commit()
        return not old or old["error"] != err

    # ----- review -----
    def awaiting(self):
        """Jobs with at least one post waiting for approval, newest last."""
        return self.db.execute("SELECT DISTINCT j.* FROM jobs j JOIN posts p ON p.job_id = j.id WHERE p.status = 'awaiting' ORDER BY j.id").fetchall()

    def approve(self, job_id, platforms=None, now=False):
        """Awaiting -> queued (all platforms, or just the ones named). A scheduled time is kept unless now=True."""
        n = self._move(job_id, platforms, ("awaiting",), "queued", None)
        if n and now: self.expedite(job_id)
        return n

    def expedite(self, job_id):
        """Make a job with queued posts due immediately (skips a scheduled time or the review delay). Returns 1 if it changed."""
        n = self.db.execute("UPDATE jobs SET publish_at = ? WHERE id = ? AND publish_at > ? AND EXISTS "
                            "(SELECT 1 FROM posts WHERE job_id = jobs.id AND status = 'queued')", (self.now(), job_id, self.now())).rowcount
        self.db.commit()
        return n

    def reject(self, job_id, platforms=None, reason=None):
        """Awaiting, queued (not started yet) or failed -> rejected. Posts already published or running are left alone."""
        return self._move(job_id, platforms, ("awaiting", "queued", "failed"), "rejected", reason or "rejected")

    def _move(self, job_id, platforms, from_status, to, note):
        q = f"UPDATE posts SET status=?, updated=?{', note=?' if note else ''} WHERE job_id=? AND status IN ({','.join('?' * len(from_status))})"
        args = [to, self.now()] + ([note] if note else []) + [job_id] + list(from_status)
        if platforms: q += f" AND platform IN ({','.join('?' * len(platforms))})"; args += list(platforms)
        n = self.db.execute(q, args).rowcount; self.db.commit()
        return n

    def set_spec(self, job_id, spec):
        self.db.execute("UPDATE jobs SET spec = ? WHERE id = ?", (json.dumps(spec), job_id)); self.db.commit()

    def requeue(self, job_id=None):
        """Put failed posts back in the queue with their attempt count cleared."""
        q, args = "UPDATE posts SET status='queued', attempts=0, updated=? WHERE status='failed'", [self.now()]
        if job_id: q += " AND job_id=?"; args.append(job_id)
        self.db.execute(q, args); self.db.commit()

    def recover(self, older_than=3600):
        """Posts left 'running' by a crash go back to queued (they will be retried)."""
        self.db.execute("UPDATE posts SET status='queued' WHERE status='running' AND updated < ?", (self.now() - older_than,)); self.db.commit()

    def status(self, limit=30, account=None):
        q = ("SELECT j.id AS job_id, j.video, j.publish_at, j.account, p.platform, p.status, p.attempts, p.url, p.note, p.error "
             "FROM posts p JOIN jobs j ON j.id = p.job_id")
        args = []
        if account: q += " WHERE j.account = ?"; args.append(account)
        return self.db.execute(q + " ORDER BY j.id DESC, p.platform LIMIT ?", args + [limit]).fetchall()

    def get(self, key, default=None):
        r = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return r["value"] if r else default

    def put(self, key, value):
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value))); self.db.commit()
