"""Posting Reels (and trial reels) with the Instagram API with Instagram Login.

Flow from Meta's Content Publishing docs:
  1. POST /<IG_ID>/media  (media_type=REELS, video_url, caption, trial_params)  -> container id
  2. GET  /<container>?fields=status_code   until FINISHED
  3. POST /<IG_ID>/media_publish (creation_id)  -> media id
"""
from __future__ import annotations

import time

import requests

GRAPH = "https://graph.instagram.com/v26.0"


def _check(r: requests.Response) -> dict:
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Instagram API error: {data.get('error', data)}")
    return data


def publish_reel(user_id: str, token: str, video_url: str, caption: str,
                 trial: bool = True, graduation: str = "MANUAL") -> str:
    headers = {"Authorization": f"Bearer {token}"}
    body = {"media_type": "REELS", "video_url": video_url, "caption": caption}
    if trial:
        body["trial_params"] = {"graduation_strategy": graduation}
    else:
        body["share_to_feed"] = True
    container = _check(requests.post(f"{GRAPH}/{user_id}/media", json=body, headers=headers, timeout=60))["id"]

    # Instagram downloads and processes the video; poll until ready (docs: about once a minute, max ~5 min).
    status = "IN_PROGRESS"
    for _ in range(20):
        time.sleep(20)
        status = _check(requests.get(f"{GRAPH}/{container}", params={"fields": "status_code,status"},
                                     headers=headers, timeout=30)).get("status_code")
        if status in ("FINISHED", "ERROR", "EXPIRED"):
            break
    if status != "FINISHED":
        raise RuntimeError(f"Video processing did not finish (status: {status})")

    return _check(requests.post(f"{GRAPH}/{user_id}/media_publish", json={"creation_id": container},
                                headers=headers, timeout=60))["id"]


def refresh_token(token: str) -> dict:
    """Long-lived tokens last 60 days; refreshing gives a fresh 60 days."""
    return _check(requests.get(f"{GRAPH.rsplit('/', 1)[0]}/refresh_access_token",
                               params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30))


def whoami(token: str) -> dict:
    return _check(requests.get(f"{GRAPH}/me", params={"fields": "user_id,username,account_type"},
                               headers={"Authorization": f"Bearer {token}"}, timeout=30))
