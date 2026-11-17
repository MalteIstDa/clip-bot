"""Art des Streams oder Videos erkennen (Gaming, Just Chatting, IRL) und Format, Hook und Hashtags daran anpassen.

Quelle ist die Kategorie: bei Twitch die eingestellte Kategorie des Streams ("Just Chatting", "IRL" oder der
Name des Spiels), bei YouTube die Videokategorie ("Gaming", ...). Ohne Kategorie entscheidet der Titel, sonst
bleibt alles wie in den Einstellungen.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("clipbot")

GENRES = ("gaming", "chatting", "irl")
LABELS = {"gaming": "Gaming", "chatting": "Just Chatting", "irl": "IRL"}

# Twitch-Kategorien, die kein Spiel sind (klein geschrieben)
_CHATTING = {"just chatting", "talk shows & podcasts", "asmr", "pools, hot tubs, and beaches", "music", "art",
             "makers & crafting", "science & technology", "software and game development", "special events",
             "politics", "crypto", "beauty & body art", "always on", "animals, aquariums, and zoos",
             "people & blogs", "entertainment", "comedy", "news & politics", "education", "howto & style",
             "film & animation", "music"}
_IRL = {"irl", "travel & outdoors", "food & drink", "sports", "fitness & health", "travel & events",
        "autos & vehicles", "pets & animals"}
_IRL_WORDS = re.compile(r"\b(irl|outdoor|draußen|draussen|unterwegs|vlog|travel|reise|roadtrip|city|stadt|"
                        r"in real life|walk|spaziergang|trip|urlaub|mallorca|dubai|tokyo|new york)\b", re.I)
_GAMING_WORDS = re.compile(r"\b(gaming|zocken|game|gameplay|ranked|fortnite|minecraft|gta|valorant|cs2|"
                           r"league of legends|lol|apex|warzone|fifa|ea fc|rocket league|elden ring|speedrun)\b", re.I)

# Anweisungen fuer die KI je Art
HOOK_HINTS = {
    "gaming": "Gaming-Clip: Der Hook nennt den Spielmoment (Clutch, Fail, Rage, unmoeglicher Move, Glueck) und "
              "das Spiel, wenn es bekannt ist. Hashtags zum Spiel und zu Gaming.",
    "chatting": "Just-Chatting-Clip: Es zaehlt die Reaktion oder Aussage des Streamers (Lachflash, Ausraster, "
                "Hot Take, Story). Der Hook zitiert oder fasst die Aussage zugespitzt zusammen. "
                "Hashtags zum Streamer, zu Streams und zum Thema.",
    "irl": "IRL-Clip: Es zaehlt die Situation draussen (Begegnung, Chaos, peinlicher oder gefaehrlicher Moment). "
           "Der Hook beschreibt die Situation und den Ort, wenn er bekannt ist. Hashtags zu IRL, Ort und Thema.",
}
BASE_TAGS = {"gaming": ["gaming", "twitchclips"], "chatting": ["justchatting", "streamer"], "irl": ["irl", "streamer"]}


def classify(category: str | None = None, title: str = "") -> str | None:
    """Ordnet eine Kategorie (und notfalls den Titel) einer Art zu: gaming | chatting | irl | None."""
    cat = (category or "").strip().lower()
    if cat:
        if cat in _IRL:
            return "irl"
        if cat == "gaming":
            return "gaming"
        if cat in _CHATTING:
            # YouTube-Kategorien wie "People & Blogs" sind unscharf: der Titel darf IRL oder Gaming verraten
            if cat in ("people & blogs", "entertainment", "comedy") and title:
                return classify(None, title) or "chatting"
            return "chatting"
        return "gaming"  # jede andere Twitch-Kategorie ist ein Spiel
    if title:
        if _IRL_WORDS.search(title):
            return "irl"
        if _GAMING_WORDS.search(title):
            return "gaming"
    return None


def game_of(category: str | None, genre: str | None) -> str:
    """Name des Spiels, wenn die Kategorie eines ist (sonst leer)."""
    cat = (category or "").strip()
    return cat if genre == "gaming" and cat.lower() not in ("gaming", "") else ""


def hashtag(text: str) -> str:
    return re.sub(r"[^\w]", "", (text or "").lower())[:30]


def tags_for(genre: str | None, category: str | None) -> list[str]:
    """Passende Hashtags (ohne #) zur Art und zum Spiel."""
    if genre not in GENRES:
        return []
    tags = list(BASE_TAGS[genre])
    game = hashtag(game_of(category, genre))
    if game:
        tags.insert(0, game)
    return tags


def settings_for(settings: dict, genre: str | None) -> dict:
    """Einstellungen fuer diesen Clip: Bildaufbau je Art (wenn eingeschaltet)."""
    if genre not in GENRES or not settings.get("genre_formats", True):
        return settings
    layout = settings.get(f"layout_{genre}") or settings["layout"]
    return {**settings, "layout": layout}


def describe(genre: str | None, category: str | None) -> str:
    """Kurzer Text fuer Dashboard und KI, z. B. "Gaming (Fortnite)"."""
    if genre not in GENRES:
        return ""
    game = game_of(category, genre)
    return f"{LABELS[genre]} ({game})" if game else LABELS[genre]


# ---------------------------------------------------------------- Kategorie abfragen
_TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # oeffentliche Kennung der Twitch-Webseite


def twitch_category(login: str, timeout: float = 10) -> str | None:
    """Aktuelle Kategorie eines Twitch-Kanals (ohne Schluessel). None, wenn offline oder nicht abfragbar."""
    import requests

    query = {"query": 'query{user(login:"%s"){stream{game{displayName name}}}}' % re.sub(r"[^\w]", "", login)}
    try:
        r = requests.post("https://gql.twitch.tv/gql", json=query, timeout=timeout,
                          headers={"Client-ID": _TWITCH_CLIENT_ID})
        game = (((r.json().get("data") or {}).get("user") or {}).get("stream") or {}).get("game") or {}
        name = game.get("displayName") or game.get("name")
        if name:
            return name
    except Exception as exc:
        log.debug("Twitch-Kategorie %s: %s", login, exc)
    try:  # Ersatz: oeffentlicher Dienst decapi.me
        r = requests.get(f"https://decapi.me/twitch/game/{re.sub(r'[^\w]', '', login)}", timeout=timeout)
        text = r.text.strip()
        if r.ok and text and len(text) < 80 and "not found" not in text.lower() and "error" not in text.lower():
            return text
    except Exception as exc:
        log.debug("Twitch-Kategorie (decapi) %s: %s", login, exc)
    return None


def category_from_info(info: dict, at: float | None = None) -> str | None:
    """Kategorie aus den Daten von yt-dlp: YouTube "categories", Twitch-Aufzeichnungen ueber die Kapitel
    (jedes Kapitel ist eine Kategorie bzw. ein Spiel). at = Sekunde im Video, fuer das passende Kapitel."""
    chapters = info.get("chapters") or []
    if chapters and info.get("extractor_key", "").lower().startswith("twitch"):
        if at is not None:
            for ch in chapters:
                if (ch.get("start_time") or 0) <= at < (ch.get("end_time") or 1e18) and ch.get("title"):
                    return ch["title"]
        longest = max(chapters, key=lambda c: (c.get("end_time") or 0) - (c.get("start_time") or 0))
        if longest.get("title"):
            return longest["title"]
    cats = info.get("categories") or []
    return cats[0] if cats else None
