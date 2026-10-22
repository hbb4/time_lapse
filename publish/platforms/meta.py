"""Shared pieces for Instagram and Facebook (both use the Meta Graph API with one Page access token)."""
from .. import config

GRAPH = "https://graph.facebook.com"
LOGIN = "meta"

def creds(tokens, account="default"):
    """{access_token, page_id, ig_user_id} for an account. For the default account, environment variables win over what
    `auth meta` saved; named accounts only use what `auth meta --account NAME` saved."""
    t = tokens.get(config.token_key(LOGIN, account))
    if account != "default": return dict(access_token=t.get("access_token"), page_id=t.get("page_id"), ig_user_id=t.get("ig_user_id"))
    return dict(access_token=config.env("META_PAGE_TOKEN") or t.get("access_token"),
                page_id=config.env("FB_PAGE_ID") or t.get("page_id"),
                ig_user_id=config.env("IG_USER_ID") or t.get("ig_user_id"))

def auth_hint(account):
    return "python -m publish auth meta" + ("" if account == "default" else f" --account {account}")

def base(settings):
    return f"{GRAPH}/{settings['meta_api_version']}"

def rupload(http, url, token, path):
    """Upload the whole file to a rupload.facebook.com session (Reels use this instead of a public video URL)."""
    data = path.read_bytes()
    return http.post(url, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))}, data=data).json()
