"""Shared pieces for Instagram and Facebook (both use the Meta Graph API with one Page access token)."""
from .. import config

GRAPH = "https://graph.facebook.com"
LOGIN = "meta"

def creds(tokens):
    """{access_token, page_id, ig_user_id}: environment variables win over what `auth meta` saved."""
    t = tokens.get(LOGIN)
    return dict(access_token=config.env("META_PAGE_TOKEN") or t.get("access_token"),
                page_id=config.env("FB_PAGE_ID") or t.get("page_id"),
                ig_user_id=config.env("IG_USER_ID") or t.get("ig_user_id"))

def base(settings):
    return f"{GRAPH}/{settings['meta_api_version']}"

def rupload(http, url, token, path):
    """Upload the whole file to a rupload.facebook.com session (Reels use this instead of a public video URL)."""
    data = path.read_bytes()
    return http.post(url, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))}, data=data).json()
