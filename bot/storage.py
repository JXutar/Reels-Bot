"""Temporary public hosting for videos (Cloudinary free plan).

Instagram Login can't take direct uploads for Reels; Instagram must download the video
from a public URL. We upload to Cloudinary, hand Instagram the URL, then delete it after posting.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests

from .config import env


def _creds():
    return env("CLOUDINARY_CLOUD_NAME"), env("CLOUDINARY_API_KEY"), env("CLOUDINARY_API_SECRET")


def _sign(params: dict, secret: str) -> str:
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.sha1((to_sign + secret).encode()).hexdigest()


def upload(path: Path, public_id: str) -> dict:
    cloud, key, secret = _creds()
    params = {"public_id": f"reels-bot/{public_id}", "overwrite": "true", "timestamp": int(time.time())}
    params["signature"] = _sign(params, secret)
    params["api_key"] = key
    with open(path, "rb") as f:
        r = requests.post(f"https://api.cloudinary.com/v1_1/{cloud}/video/upload", data=params,
                          files={"file": f}, timeout=300)
    r.raise_for_status()
    data = r.json()
    return {"video_url": data["secure_url"], "public_id": data["public_id"]}


def delete(public_id: str) -> None:
    if not public_id:
        return
    cloud, key, secret = _creds()
    params = {"public_id": public_id, "timestamp": int(time.time())}
    params["signature"] = _sign(params, secret)
    params["api_key"] = key
    params["resource_type"] = "video"
    requests.post(f"https://api.cloudinary.com/v1_1/{cloud}/video/destroy", data=params, timeout=60)
