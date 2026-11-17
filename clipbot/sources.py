"""Quellen im Netz: Plattform erkennen, neueste Videos auflisten, Live-Status abfragen (yt-dlp, ohne API-Schluessel).

Alle Netzzugriffe stehen in diesem Modul, damit Tests sie durch Attrappen ersetzen koennen.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse
from xml.etree import ElementTree

YT_TABS = ("/videos", "/streams", "/shorts", "/live", "/featured", "/playlists")
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_OFFLINE = ("not currently live", "is offline", "offline", "not live", "does not exist")
PLATFORM_LABELS = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick", "other": "Andere Seite"}


class SourceError(Exception):
    """Die Quelle konnte nicht abgefragt werden."""


def platform_of(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").lower()
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith("twitch.tv"):
        return "twitch"
    if host.endswith("kick.com"):
        return "kick"
    return "other"


def clean_url(url: str) -> str:
    """Adresse eines Kanals ohne Tab und ohne Anhaengsel (fuer den Vergleich und zum Speichern)."""
    url = str(url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    if platform_of(url) == "youtube":
        for tab in YT_TABS:
            if url.endswith(tab):
                url = url[: -len(tab)]
    if platform_of(url) == "twitch":
        url = re.sub(r"/(videos|clips|about|schedule)$", "", url)
    return url


def name_from_url(url: str) -> str:
    """Anzeigename aus der Adresse: youtube.com/@Gtasty -> Gtasty, twitch.tv/papaplatte -> papaplatte."""
    path = urlparse(clean_url(url)).path.strip("/")
    last = path.split("/")[-1] if path else ""
    return last.lstrip("@") or (urlparse(url).hostname or "creator")


def videos_url(url: str) -> str:
    """Adresse, unter der die neuesten Videos bzw. Aufzeichnungen zuerst stehen."""
    base = clean_url(url)
    platform = platform_of(base)
    if platform == "youtube" and re.search(r"youtube\.com/(@|channel/|c/|user/)", base):
        return base + "/videos"
    if platform == "twitch":
        return base + "/videos?filter=archives&sort=time"
    return str(url).strip()


def _flat(url: str, limit: int) -> dict:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "playlistend": limit, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _entries(info: dict, limit: int) -> list[dict]:
    flat: list[dict] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        if entry.get("entries"):  # verschachtelte Tabs
            flat.extend(e for e in entry["entries"] if e)
        else:
            flat.append(entry)
    videos = []
    for e in flat[:limit]:
        if e.get("id"):
            videos.append({
                "id": str(e["id"]),
                "title": e.get("title") or str(e["id"]),
                "url": e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e['id']}",
                "live_status": e.get("live_status"),
                "duration": e.get("duration"),
            })
    return videos


def list_latest(source_url: str, limit: int) -> list[dict]:
    """Die neuesten Videos eines Kanals: [{"id", "title", "url", "live_status", "duration"}], neueste zuerst."""
    return _entries(_flat(videos_url(source_url), limit), limit)


def youtube_streams(url: str, limit: int = 5) -> list[dict]:
    """Streams eines YouTube-Kanals (laufend, angekuendigt, vergangen) mit live_status."""
    return _entries(_flat(clean_url(url) + "/streams", limit), limit)


def youtube_channel_id(url: str) -> str | None:
    """Die Kanal-ID (UC...) eines YouTube-Kanals; noetig fuer den RSS-Feed."""
    match = re.search(r"youtube\.com/channel/(UC[\w-]{20,})", url)
    if match:
        return match[1]
    info = _flat(videos_url(url), 1)
    return info.get("channel_id") or next(
        (e.get("channel_id") for e in info.get("entries") or [] if e and e.get("channel_id")), None)


def parse_feed(xml_text: str) -> list[dict]:
    """YouTube-RSS-Feed -> [{"id", "title", "url", "published"}], neueste zuerst."""
    root = ElementTree.fromstring(xml_text)
    out = []
    for entry in root.iter(f"{_ATOM}entry"):
        video_id = entry.findtext(f"{_YT}videoId")
        if not video_id:
            continue
        published = None
        try:
            published = datetime.fromisoformat(entry.findtext(f"{_ATOM}published") or "").timestamp()
        except ValueError:
            pass
        out.append({"id": video_id, "title": entry.findtext(f"{_ATOM}title") or video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}", "published": published})
    return out


def youtube_feed(channel_id: str, timeout: float = 15) -> list[dict]:
    """Die letzten 15 Uploads und Streams eines Kanals ueber den RSS-Feed (eine kleine Anfrage, kein Schluessel)."""
    import requests

    response = requests.get(FEED.format(cid=channel_id), timeout=timeout)
    if response.status_code != 200:
        raise SourceError(f"YouTube-Feed antwortet mit {response.status_code}")
    return parse_feed(response.text)


def probe(url: str) -> dict:
    """Einzelnes Video abfragen: {"id", "title", "live_status", "duration", "uploader", "url"}.
    live_status: is_upcoming | is_live | post_live | was_live | not_live | None."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "ignore_no_formats_error": True}  # angekuendigte Streams haben noch keine Formate
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        raise SourceError(str(exc)[:300]) from exc
    status = info.get("live_status")
    if not status and info.get("is_live"):
        status = "is_live"
    return {"id": str(info.get("id") or ""), "title": info.get("title") or "", "live_status": status,
            "duration": info.get("duration"), "uploader": info.get("channel") or info.get("uploader") or "",
            "url": info.get("webpage_url") or url, "categories": info.get("categories") or []}


def live_now(url: str) -> dict | None:
    """Laeuft auf dieser Kanal-Adresse (Twitch, Kick, ...) gerade ein Stream? Dann {"id", "title", "url"}, sonst None."""
    try:
        info = probe(clean_url(url))
    except SourceError as exc:
        if any(hint in str(exc).lower() for hint in _OFFLINE):
            return None
        raise
    if info["live_status"] != "is_live":
        return None
    return {"id": info["id"], "title": info["title"], "url": clean_url(url)}
