"""Virale Erklaer- und KI-Videos auf YouTube finden und daraus eigene Themenideen ableiten."""
from __future__ import annotations

import logging
from urllib.parse import quote_plus

from clipbot import ai, search

log = logging.getLogger("clipbot")

THIS_MONTH = "EgQIBBAB"  # Suchfilter: Typ "Video", hochgeladen in diesem Monat


def find_trending(queries: list[str], per_query: int = 25, max_seconds: int = 180, limit: int = 20) -> list[dict]:
    """Kurze Videos dieses Monats zu den Suchbegriffen, sortiert nach Aufrufen."""
    seen, found = set(), []
    for query in queries:
        try:
            entries = search._entries(search.RESULTS.format(q=quote_plus(query), sp=THIS_MONTH), per_query)
        except Exception as exc:  # Netz, yt-dlp-Fehler: den naechsten Begriff versuchen
            log.info("Trendsuche '%s' fehlgeschlagen: %s", query, exc)
            continue
        for e in entries:
            vid = e.get("id")
            duration = e.get("duration") or 0
            if not vid or vid in seen or (duration and duration > max_seconds):
                continue
            seen.add(vid)
            found.append({
                "id": vid, "title": e.get("title") or "", "views": int(e.get("view_count") or 0),
                "channel": e.get("channel") or e.get("uploader") or "", "seconds": duration,
                "url": f"https://www.youtube.com/shorts/{vid}" if duration and duration <= 60
                       else f"https://www.youtube.com/watch?v={vid}",
                "query": query,
            })
    return sorted(found, key=lambda v: v["views"], reverse=True)[:limit]


IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "hook": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["topic", "hook", "why"],
            },
        },
    },
    "required": ["ideas"],
}


def ideas(settings: dict, trending: list[dict], themes: list[str], count: int = 8) -> list[dict]:
    """Eigene Themenideen aus den Trends ableiten (keine Kopien einzelner Videos)."""
    listing = "\n".join(f"- {v['title']} ({v['views']:,} Aufrufe)".replace(",", ".") for v in trending[:20])
    prompt = (
        "Du planst kurze deutsche Erklaervideos (45 bis 75 Sekunden, Hochformat) fuer TikTok, YouTube Shorts und Reels.\n"
        f"Themenfelder des Kanals: {', '.join(themes)}.\n\n"
        f"Diese kurzen Videos laufen gerade gut:\n{listing or '(keine Trenddaten)'}\n\n"
        f"Leite daraus {count} eigene Themen ab. Erkenne, welche Art Frage zieht (z. B. 'Was passiert, wenn ...'), "
        "und uebertrage das Muster auf neue Fragen. Kopiere keinen einzelnen Titel. Jedes Thema muss sich mit "
        "gesichertem Wissen sachlich korrekt beantworten lassen; keine Verschwoerungen, keine Gesundheitsratschlaege.\n"
        "topic: das Thema als Frage. hook: der erste gesprochene Satz (packend, max. 12 Woerter). "
        "why: kurz, welcher Trend dahintersteht."
    )
    data = ai.generate_json(settings, prompt, IDEAS_SCHEMA)
    return data["ideas"][:count]
