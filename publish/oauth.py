"""OAuth 2.0 authorization-code login (with PKCE) for the platforms that use it.

Opens the browser, catches the redirect on http://localhost:<port>/callback and trades the code for tokens.
If the app's registered redirect URI is not localhost, use paste mode: log in, then paste the address the
browser ends up on (it will fail to load; that is fine) back into the terminal.
"""
import base64, hashlib, http.server, secrets, time, urllib.parse, webbrowser

import requests

def pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge

def authorize(auth_url, token_url, client_id, scopes, client_secret=None, redirect_uri="http://localhost:8765/callback",
              id_param="client_id", scope_sep=" ", extra=None, token_auth="body", use_pkce=True, paste=False, open_browser=True,
              say=print):
    """Run the login and return a token dict: access_token, refresh_token, expires_at, plus whatever else came back."""
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    q = {id_param: client_id, "response_type": "code", "redirect_uri": redirect_uri, "scope": scope_sep.join(scopes), "state": state}
    if use_pkce: q.update(code_challenge=challenge, code_challenge_method="S256")
    q.update(extra or {})
    url = auth_url + ("&" if "?" in auth_url else "?") + urllib.parse.urlencode(q)
    say(f"Open this address and approve access:\n  {url}")
    if open_browser and not paste: webbrowser.open(url)
    parsed = urllib.parse.urlparse(redirect_uri)
    if paste or parsed.hostname not in ("localhost", "127.0.0.1"):
        got = urllib.parse.parse_qs(urllib.parse.urlparse(input("Paste the full address you were sent to: ").strip()).query)
    else:
        got = _catch(parsed.port or 80, parsed.path or "/")
    if got.get("state", [""])[0] != state: raise RuntimeError("login state did not match; start again")
    if "code" not in got: raise RuntimeError(f"login failed: {got.get('error_description') or got.get('error') or got}")
    form = {"grant_type": "authorization_code", "code": got["code"][0], "redirect_uri": redirect_uri}
    if use_pkce: form["code_verifier"] = verifier
    form["client_key" if id_param == "client_key" else "client_id"] = client_id
    auth = None
    if client_secret:
        if token_auth == "basic": auth = (client_id, client_secret)
        else: form["client_secret"] = client_secret
    r = requests.post(token_url, data=form, auth=auth, timeout=60)
    if r.status_code != 200: raise RuntimeError(f"token exchange failed: {r.status_code} {r.text[:300]}")
    tok = r.json()
    tok = tok.get("data", tok)
    if "expires_in" in tok: tok["expires_at"] = time.time() + float(tok["expires_in"])
    return tok

def _catch(port, path):
    box = {}
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == path: box.update(urllib.parse.parse_qs(u.query))
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
            self.wfile.write(b"Logged in. You can close this tab.")
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    srv.timeout = 1
    end = time.time() + 300
    while not box and time.time() < end: srv.handle_request()
    srv.server_close()
    if not box: raise RuntimeError("timed out waiting for the login redirect")
    return box
