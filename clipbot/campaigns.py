"""Clip-Kampagnen: Creator, die Clipper fuer Aufrufe bezahlen (z. B. gtastyclips.com).

Hat ein Creator eine aktive Kampagne, macht ClipBot aus seinen Streams und Videos wieder einzelne Clips statt
Zusammenschnitten, schreibt die Pflicht-Hashtags der Kampagne (z. B. #gtasty) unter jeden Clip und postet sie
sofort auf TikTok und YouTube. Die Clips liegen im Dashboard-Bereich "Kampagnen".

Gesucht wird einmal am Tag je Creator der Watchlist, und zwar auf typischen Kampagnen-Seiten wie
<name>clips.com. Kampagnen auf Plattformen mit Anmeldung (z. B. Whop) findet die Suche nicht, die traegst du
im Dashboard von Hand ein.
"""
from __future__ import annotations

import html
import logging
import re
import time
from urllib.parse import urlparse

from clipbot import config, notify

log = logging.getLogger("clipbot")
SEARCH_EVERY = 24 * 3600
DOMAINS = ("{}clips.com", "{}clipping.com", "{}clips.de", "{}clip.com")
KEYWORDS = re.compile(r"clip|views|aufrufe|campaign|kampagne|payout|auszahl|cpm|reward|verdien|earn", re.I)
HASHTAG = re.compile(r"(?<![\w&/#])#([A-Za-z][A-Za-z0-9_]{1,29})\b")
HEX = re.compile(r"[0-9a-fA-F]{3,8}")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}
PLATFORMS = ("tiktok", "youtube")  # Kampagnen-Clips gehen nie auf Instagram


# ---------------------------------------------------------------- Konfiguration
def of_creator(cfg: dict, creator_id: str) -> dict | None:
    """Die aktive Kampagne des Creators oder None."""
    return next((c for c in cfg.get("campaigns") or [] if c.get("creator") == creator_id and c.get("active", True)),
                None)


def get(cfg: dict, campaign_id: str) -> dict | None:
    return next((c for c in cfg.get("campaigns") or [] if c.get("id") == campaign_id), None)


def tags_of(campaign: dict) -> list[str]:
    """Pflicht-Hashtags als Liste mit #, ohne doppelte."""
    out, seen = [], set()
    for word in re.split(r"[\s,;]+", str(campaign.get("hashtags") or "")):
        word = word.strip().lstrip("#")
        if re.fullmatch(r"[A-Za-z0-9_]{1,40}", word) and word.lower() not in seen:
            seen.add(word.lower())
            out.append("#" + word)
    return out


def clean_hashtags(text: str) -> str:
    return " ".join(tags_of({"hashtags": text}))


def apply_tags(texts: dict, campaign: dict) -> dict:
    """Haengt die Pflicht-Hashtags an jede Beschreibung (allgemein und je Plattform), falls sie fehlen."""
    tags = tags_of(campaign)
    if not tags:
        return texts

    def add(caption: str) -> str:
        have = {t.lower() for t in re.findall(r"#\w+", caption or "")}
        missing = [t for t in tags if t.lower() not in have]
        if not missing:
            return caption
        caption = (caption or "").rstrip()
        return (caption + ("\n\n" if caption else "") + " ".join(missing)).strip()

    out = {**texts, "caption": add(texts.get("caption", ""))}
    platforms = {}
    for platform, own in (texts.get("platforms") or {}).items():
        own = dict(own or {})
        if own.get("caption"):
            own["caption"] = add(own["caption"])
        platforms[platform] = own
    out["platforms"] = platforms
    return out


# ---------------------------------------------------------------- Suche
def slugs(creator: dict) -> list[str]:
    """Moegliche Kurznamen des Creators: Anzeigename und Name aus der Adresse, nur Buchstaben und Ziffern."""
    names = [str(creator.get("name") or ""), str(creator.get("id") or "")]
    path = urlparse(str(creator.get("url") or "")).path.strip("/").split("/")
    if path and path[0]:
        names.append(path[-1] if path[0] in ("c", "user", "channel") else path[0])
    out = []
    for name in names:
        s = re.sub(r"[^a-z0-9]", "", name.lower().lstrip("@"))
        if 3 <= len(s) <= 30 and s not in out:
            out.append(s)
    return out


def candidate_urls(creator: dict) -> list[str]:
    return [f"https://{d.format(s)}" for s in slugs(creator) for d in DOMAINS]


def page_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))


def hashtags_in(text: str, slug: str) -> list[str]:
    """Hashtags auf der Seite. Die mit dem Namen des Creators zuerst; Farbcodes wie #fff fallen raus."""
    found = []
    for tag in HASHTAG.findall(text):
        if HEX.fullmatch(tag) or tag.lower() in (t.lower() for t in found):
            continue
        found.append(tag)
    found.sort(key=lambda t: slug not in t.lower())
    return ["#" + t for t in found if slug in t.lower()][:3] or ["#" + slug]


def probe(url: str, slug: str, fetch=None) -> dict | None:
    """Schaut, ob unter der Adresse eine Clip-Kampagne des Creators liegt. Gibt {"url", "hashtags", "title"} zurueck."""
    if fetch is None:
        import requests

        def fetch(u):
            r = requests.get(u, headers=UA, timeout=12, allow_redirects=True)
            return r.status_code, r.url, r.text[:400_000]
    try:
        status, final, raw = fetch(url)
    except Exception:
        return None
    if status != 200 or slug not in urlparse(final).netloc.lower().replace("-", ""):
        return None  # nicht erreichbar oder weitergeleitet zu einer fremden Seite (z. B. Domain-Parkplatz)
    text = page_text(raw)
    if slug not in text.lower().replace(" ", "") or len(KEYWORDS.findall(text)) < 2:
        return None
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    return {"url": final.rstrip("/") or url, "hashtags": " ".join(hashtags_in(text, slug)),
            "title": html.unescape(title.group(1)).strip()[:80] if title else ""}


def search_creator(creator: dict, fetch=None) -> dict | None:
    for s in slugs(creator):
        for domain in DOMAINS:
            hit = probe(f"https://{domain.format(s)}", s, fetch)
            if hit:
                return hit
    return None


def discover(cfg: dict, state, fetch=None, force: bool = False, now: float | None = None,
             limit: int = 5) -> list[dict]:
    """Sucht fuer Creator der Watchlist (je Creator hoechstens einmal am Tag, je Durchgang hoechstens `limit`)
    nach einer Kampagne und traegt Funde ein. Gibt die neuen Kampagnen zurueck."""
    now = now or time.time()
    known = {c.get("creator") for c in cfg.get("campaigns") or []}
    found, searched = [], 0
    for creator in cfg.get("creators") or []:
        if creator["id"] in known or searched >= limit:
            continue
        key = f"campaign_search:{creator['id']}"
        last = state.get_meta(key)
        if not force and last is not None and now - float(last) < SEARCH_EVERY:
            continue
        state.set_meta(key, str(now))
        searched += 1
        hit = search_creator(creator, fetch)
        if hit:
            found.append({"id": creator["id"], "creator": creator["id"], "name": creator.get("name") or creator["id"],
                          "url": hit["url"], "hashtags": hit["hashtags"], "active": True, "found": "auto"})
    if not found:
        return []
    with config.LOCK:
        raw = config.load_raw()
        have = {c.get("creator") for c in raw.get("campaigns") or []}
        new = [c for c in found if c["creator"] not in have]
        for c in new:
            c["id"] = config._unique(c["id"], {x["id"] for x in raw["campaigns"]})
            raw["campaigns"].append(c)
        if new:
            config.save(raw)
    for c in new:
        log.info("[Kampagnen] Gefunden: %s (%s), Hashtags %s", c["name"], c["url"], c["hashtags"])
        notify.send(state, cfg["settings"], "clips", f"Clip-Kampagne gefunden: {c['name']}",
                    f"{c['url']} – Clips bekommen {c['hashtags']} und werden sofort gepostet.")
    return new


# ---------------------------------------------------------------- Posten
def due(cfg: dict, state, now: float, retry_seconds: float) -> list[tuple[dict, dict, list]]:
    """Kampagnen-Clips, die ein TikTok- oder YouTube-Konto noch nicht gepostet hat: [(konto, kanal, [clips])]."""
    from pathlib import Path
    out = []
    for account in cfg.get("accounts") or []:
        if account.get("platform") not in PLATFORMS or not config.account_connected(account):
            continue
        if state.failed_posts_since(account["id"], now - retry_seconds):
            continue
        for channel in config.channels_of_account(cfg, account["id"]):
            exclusive = channel.get("distribute") == "abwechselnd"
            clips = [dict(c) for c in state.pending_clips(channel["name"], account["id"], account["platform"], exclusive)
                     if (c["kind"] or "") == "campaign" and Path(c["path"]).exists()]
            if clips:
                out.append((account, channel, clips))
    return out
