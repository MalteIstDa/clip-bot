"""Upload als Instagram Reel ueber die offizielle Graph API (Resumable Upload, keine oeffentliche URL noetig)."""
from __future__ import annotations

import time
from pathlib import Path

import requests

from .base import UploadError


def _check(r: requests.Response, step: str) -> dict:
    try:
        payload = r.json()
    except ValueError:
        raise UploadError(f"Instagram {step}: HTTP {r.status_code} {r.text[:200]}")
    if r.status_code >= 400 or "error" in payload or payload.get("success") is False:
        detail = payload.get("error", {}).get("message") or payload.get("debug_info") or payload
        raise UploadError(f"Instagram {step}: {detail}")
    return payload


def whoami(ig_user_id: str, access_token: str, api_version: str = "v26.0") -> str:
    r = requests.get(f"https://graph.facebook.com/{api_version}/{ig_user_id}",
                     params={"fields": "username", "access_token": access_token}, timeout=30)
    return _check(r, "Kontopruefung")["username"]


def upload(video_path: Path, caption: str, ig_user_id: str, access_token: str,
           api_version: str = "v26.0") -> str:
    graph = f"https://graph.facebook.com/{api_version}"

    container = _check(requests.post(f"{graph}/{ig_user_id}/media", timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
        "share_to_feed": "true", "access_token": access_token}), "Container anlegen")
    container_id = container["id"]
    upload_url = container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{api_version}/{container_id}"

    size = video_path.stat().st_size
    with open(video_path, "rb") as fh:
        _check(requests.post(upload_url, data=fh, timeout=900, headers={
            "Authorization": f"OAuth {access_token}", "offset": "0", "file_size": str(size)}), "Datei-Upload")

    for _ in range(60):  # bis zu 5 Minuten auf die Verarbeitung warten
        time.sleep(5)
        status = _check(requests.get(f"{graph}/{container_id}", timeout=30, params={
            "fields": "status_code,status", "access_token": access_token}), "Status")
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") in ("ERROR", "EXPIRED"):
            raise UploadError(f"Instagram hat den Clip abgelehnt: {status.get('status')}")
    else:
        raise UploadError("Instagram: Verarbeitung dauert zu lange")

    published = _check(requests.post(f"{graph}/{ig_user_id}/media_publish", timeout=60, data={
        "creation_id": container_id, "access_token": access_token}), "Veroeffentlichen")
    return published["id"]
