"""Kanaele auf YouTube finden (ueber yt-dlp, ohne API-Schluessel)."""
from __future__ import annotations

from urllib.parse import quote_plus

RESULTS = "https://www.youtube.com/results?search_query={q}&sp={sp}"
ONLY_CHANNELS = "EgIQAg%3D%3D"   # Suchfilter: Typ "Kanal"
VIDEOS_THIS_WEEK = "EgQIAxAB"    # Suchfilter: Typ "Video", hochgeladen in den letzten 7 Tagen


def _entries(url: str, limit: int) -> list[dict]:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
            "playlistend": limit, "ignoreerrors": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [e for e in ((info or {}).get("entries") or []) if e]


def _thumbnail(entry: dict) -> str | None:
    thumbs = entry.get("thumbnails") or []
    url = (thumbs[-1].get("url") if thumbs else None) or entry.get("thumbnail")
    if url and url.startswith("//"):
        url = "https:" + url
    return url


def _channel_url(entry: dict) -> str | None:
    for key in ("uploader_url", "channel_url"):
        if str(entry.get(key) or "").startswith("https://"):
            return entry[key]
    if entry.get("channel_id"):
        return f"https://www.youtube.com/channel/{entry['channel_id']}"
    return None


def channels_from_entries(entries: list[dict]) -> list[dict]:
    """Treffer einer Kanalsuche in ein einheitliches Format bringen."""
    found = []
    for e in entries:
        url = _channel_url(e) or str(e.get("url") or "")
        if "/@" not in url and "/channel/" not in url:
            continue
        found.append({
            "name": e.get("channel") or e.get("title") or e.get("uploader") or url,
            "url": url,
            "handle": e.get("uploader_id") if str(e.get("uploader_id") or "").startswith("@") else None,
            "subscribers": e.get("channel_follower_count"),
            "thumbnail": _thumbnail(e),
            "description": (e.get("description") or "")[:160],
        })
    return found


def channels_from_videos(entries: list[dict]) -> list[dict]:
    """Videos nach Kanal zusammenfassen und nach Aufrufen sortieren."""
    by_channel: dict[str, dict] = {}
    for e in entries:
        url = _channel_url(e)
        if not url:
            continue
        views = int(e.get("view_count") or 0)
        ch = by_channel.setdefault(url, {
            "name": e.get("channel") or e.get("uploader") or url, "url": url,
            "handle": e.get("uploader_id") if str(e.get("uploader_id") or "").startswith("@") else None,
            "subscribers": e.get("channel_follower_count"), "thumbnail": None,
            "videos": 0, "views": 0, "top_video": None,
        })
        ch["videos"] += 1
        ch["views"] += views
        if not ch["top_video"] or views > ch["top_video"]["views"]:
            ch["top_video"] = {"title": e.get("title") or "", "views": views,
                               "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}"}
    return sorted(by_channel.values(), key=lambda c: c["views"], reverse=True)


def search_channels(query: str, limit: int = 12) -> list[dict]:
    """Kanaele nach Name suchen."""
    found = channels_from_entries(_entries(RESULTS.format(q=quote_plus(query), sp=ONLY_CHANNELS), limit))
    if not found:  # Ersatzweg: Videos suchen und deren Kanaele anzeigen
        found = channels_from_videos(_entries(f"ytsearch{limit * 2}:{query}", limit * 2))
    return found[:limit]


def trending_channels(query: str, limit: int = 12) -> list[dict]:
    """Kanaele, deren Videos zu einem Thema in den letzten 7 Tagen die meisten Aufrufe hatten."""
    entries = _entries(RESULTS.format(q=quote_plus(query), sp=VIDEOS_THIS_WEEK), 60)
    return channels_from_videos(entries)[:limit]
