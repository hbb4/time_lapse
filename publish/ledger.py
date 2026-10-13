"""SQLite record of what was queued and posted, so a post is never made twice and failures can be retried."""
import json, sqlite3, time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, sha TEXT NOT NULL, video TEXT NOT NULL, created REAL NOT NULL,
  publish_at REAL NOT NULL, spec TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS posts (
  job_id INTEGER NOT NULL REFERENCES jobs(id), platform TEXT NOT NULL,
  status TEXT NOT NULL,            -- queued | running | published | failed | skipped
  attempts INTEGER NOT NULL DEFAULT 0, remote_id TEXT, url TEXT, note TEXT, error TEXT, updated REAL NOT NULL,
  PRIMARY KEY (job_id, platform));
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);
"""

class Ledger:
    def __init__(self, path=None, now=time.time):
        path = path or config.DB_PATH
        if str(path) != ":memory:": path.parent.mkdir(parents=True, exist_ok=True)
        self.db, self.now = sqlite3.connect(str(path)), now
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def published(self, sha, platform):
        """The existing published post of this video on this platform, if any."""
        return self.db.execute("SELECT p.* FROM posts p JOIN jobs j ON j.id = p.job_id "
                               "WHERE j.sha = ? AND p.platform = ? AND p.status = 'published'", (sha, platform)).fetchone()

    def pending(self, sha, platform, max_attempts):
        """A queued, running or retryable post of this video on this platform, if any."""
        return self.db.execute("SELECT p.* FROM posts p JOIN jobs j ON j.id = p.job_id WHERE j.sha = ? AND p.platform = ? AND "
                               "(p.status IN ('queued', 'running') OR (p.status = 'failed' AND p.attempts < ?))",
                               (sha, platform, max_attempts)).fetchone()

    def enqueue(self, sha, video, spec, platforms, publish_at=None, skipped=None):
        """One job with a post row per platform; `skipped` = {platform: reason} for ones routed away."""
        now = self.now()
        cur = self.db.execute("INSERT INTO jobs (sha, video, created, publish_at, spec) VALUES (?,?,?,?,?)",
                              (sha, str(video), now, publish_at or now, json.dumps(spec)))
        jid = cur.lastrowid
        for p in platforms:
            self.db.execute("INSERT INTO posts (job_id, platform, status, updated) VALUES (?,?,?,?)", (jid, p, "queued", now))
        for p, why in (skipped or {}).items():
            self.db.execute("INSERT INTO posts (job_id, platform, status, note, updated) VALUES (?,?,?,?,?)", (jid, p, "skipped", why, now))
        self.db.commit()
        return jid

    def due(self, max_attempts):
        """(job, post) rows ready to run: queued and due, or failed with attempts left (oldest first)."""
        rows = self.db.execute(
            "SELECT j.id AS job_id, j.sha, j.video, j.spec, j.publish_at, p.platform, p.attempts FROM posts p "
            "JOIN jobs j ON j.id = p.job_id WHERE j.publish_at <= ? AND "
            "(p.status = 'queued' OR (p.status = 'failed' AND p.attempts < ?)) ORDER BY j.publish_at, j.id", (self.now(), max_attempts)).fetchall()
        return rows

    def start(self, job_id, platform):
        self.db.execute("UPDATE posts SET status='running', attempts=attempts+1, updated=? WHERE job_id=? AND platform=?",
                        (self.now(), job_id, platform)); self.db.commit()

    def done(self, job_id, platform, remote_id, url, note=None):
        self.db.execute("UPDATE posts SET status='published', remote_id=?, url=?, note=?, error=NULL, updated=? WHERE job_id=? AND platform=?",
                        (remote_id, url, note, self.now(), job_id, platform)); self.db.commit()

    def fail(self, job_id, platform, error):
        self.db.execute("UPDATE posts SET status='failed', error=?, updated=? WHERE job_id=? AND platform=?",
                        (str(error)[:1500], self.now(), job_id, platform)); self.db.commit()

    def requeue(self, job_id=None):
        """Put failed posts back in the queue with their attempt count cleared."""
        q, args = "UPDATE posts SET status='queued', attempts=0, updated=? WHERE status='failed'", [self.now()]
        if job_id: q += " AND job_id=?"; args.append(job_id)
        self.db.execute(q, args); self.db.commit()

    def recover(self, older_than=3600):
        """Posts left 'running' by a crash go back to queued (they will be retried)."""
        self.db.execute("UPDATE posts SET status='queued' WHERE status='running' AND updated < ?", (self.now() - older_than,)); self.db.commit()

    def status(self, limit=30):
        return self.db.execute("SELECT j.id AS job_id, j.video, j.publish_at, p.platform, p.status, p.attempts, p.url, p.note, p.error "
                               "FROM posts p JOIN jobs j ON j.id = p.job_id ORDER BY j.id DESC, p.platform LIMIT ?", (limit,)).fetchall()
