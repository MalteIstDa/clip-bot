"""Upload ueber die offizielle TikTok Content Posting API (Direct Post)."""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .base import UploadError

API = "https://open.tiktokapis.com/v2"
CHUNK = 10 * 1024 * 1024
SINGLE_UPLOAD_MAX = 64 * 1024 * 1024


def _token_request(data: dict) -> dict:
    r = requests.post(f"{API}/oauth/token/", data=data, timeout=30,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    payload = r.json()
    if "access_token" not in payload:
        raise UploadError(f"TikTok-Anmeldung fehlgeschlagen: {payload}")
    payload["expires_at"] = time.time() + int(payload.get("expires_in", 0)) - 300
    return payload


def authorize_url(client_key: str, redirect_uri: str) -> tuple[str, str]:
    """Schritt 1: Adresse, auf der der Kontoinhaber zustimmt. Gibt (url, state) zurueck."""
    state = secrets.token_urlsafe(16)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key": client_key, "scope": "video.publish", "response_type": "code",
        "redirect_uri": redirect_uri, "state": state,
    })
    return url, state


def finish_authorize(client_key: str, client_secret: str, redirect_uri: str, state: str,
                     redirected: str, token_file: Path) -> None:
    """Schritt 2: Code aus der Zieladresse lesen und gegen einen Token tauschen."""
    query = parse_qs(urlparse(redirected.strip()).query)
    if query.get("state", [""])[0] != state or "code" not in query:
        raise UploadError("Die Adresse enthaelt keinen gueltigen Code. Bitte erneut versuchen.")
    token = _token_request({
        "client_key": client_key, "client_secret": client_secret, "code": query["code"][0],
        "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    })
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps(token), encoding="utf-8")


def authorize(client_key: str, client_secret: str, redirect_uri: str, token_file: Path) -> None:
    """Anmeldung in der Konsole: Link oeffnen, zustimmen, Adresse der Zielseite einfuegen."""
    url, state = authorize_url(client_key, redirect_uri)
    print("\n1) Diesen Link im Browser oeffnen und mit dem passenden TikTok-Konto zustimmen:\n\n" + url)
    redirected = input("\n2) Komplette Adresse der Seite einfuegen, auf der du danach landest:\n> ")
    finish_authorize(client_key, client_secret, redirect_uri, state, redirected, token_file)


def _access_token(token_file: Path, client_key: str, client_secret: str) -> str:
    if not token_file.exists():
        raise UploadError(f"{token_file} fehlt. Zuerst 'python auth.py tiktok <kanal>' ausfuehren.")
    token = json.loads(token_file.read_text(encoding="utf-8"))
    if time.time() >= token.get("expires_at", 0):
        token = _token_request({
            "client_key": client_key, "client_secret": client_secret,
            "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
        })
        token_file.write_text(json.dumps(token), encoding="utf-8")
    return token["access_token"]


def _post(path: str, access_token: str, body: dict) -> dict:
    r = requests.post(f"{API}{path}", json=body, timeout=60, headers={
        "Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"})
    payload = r.json()
    error = payload.get("error") or {}
    if error.get("code") not in (None, "ok"):
        raise UploadError(f"TikTok {path}: {error.get('code')} - {error.get('message')}")
    return payload.get("data") or {}


def upload(video_path: Path, caption: str, token_file: Path, client_key: str, client_secret: str,
           privacy: str = "public") -> str:
    access = _access_token(token_file, client_key, client_secret)

    creator = _post("/post/publish/creator_info/query/", access, {})
    options = creator.get("privacy_level_options") or ["SELF_ONLY"]
    wanted = "PUBLIC_TO_EVERYONE" if privacy == "public" else "SELF_ONLY"
    level = wanted if wanted in options else "SELF_ONLY"

    size = video_path.stat().st_size
    chunk_size = size if size <= SINGLE_UPLOAD_MAX else CHUNK
    chunk_count = max(1, size // chunk_size)  # der letzte Teil nimmt den Rest mit auf
    init = _post("/post/publish/video/init/", access, {
        "post_info": {"title": caption[:2200], "privacy_level": level, "disable_duet": False,
                      "disable_comment": False, "disable_stitch": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                        "chunk_size": chunk_size, "total_chunk_count": chunk_count},
    })

    with open(video_path, "rb") as fh:
        for i in range(chunk_count):
            first = i * chunk_size
            last = size - 1 if i == chunk_count - 1 else first + chunk_size - 1
            data = fh.read(last - first + 1)
            r = requests.put(init["upload_url"], data=data, timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {first}-{last}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise UploadError(f"TikTok-Upload Teil {i + 1}: HTTP {r.status_code} {r.text[:200]}")

    publish_id = init["publish_id"]
    for _ in range(60):  # bis zu 5 Minuten auf die Verarbeitung warten
        time.sleep(5)
        status = _post("/post/publish/status/fetch/", access, {"publish_id": publish_id})
        if status.get("status") == "PUBLISH_COMPLETE":
            return publish_id
        if status.get("status") == "FAILED":
            raise UploadError(f"TikTok hat den Clip abgelehnt: {status.get('fail_reason')}")
    raise UploadError("TikTok: Verarbeitung dauert zu lange (Status unbekannt)")
