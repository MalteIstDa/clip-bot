"""Virale Auswahl und Texte per KI: beste Stellen waehlen, Hook, Titel, Captions und Hashtags je Plattform.

Jede Funktion faellt ohne KI (oder bei einem KI-Fehler) auf das alte Verhalten zurueck,
damit ein Lauf nie an der KI scheitert.
"""
from __future__ import annotations

import logging
import re

from clipbot import ai

log = logging.getLogger("clipbot")

SYSTEM = (
    "Du bist ein erfahrener Short-Form-Editor fuer TikTok, YouTube Shorts und Instagram Reels. "
    "Du erkennst Momente, die Zuschauer in den ersten 2 Sekunden fesseln und bis zum Ende schauen lassen: "
    "starker Einstieg, Spannung oder Konflikt, Pointe oder Ueberraschung, ohne Vorwissen verstaendlich. "
    "Du schreibst Texte, die neugierig machen, ohne zu luegen oder Clickbait zu versprechen, was der Clip nicht haelt. "
    "Antworte nur mit JSON nach dem Schema."
)


# ---------------------------------------------------------------- Text-Helfer ohne KI
def spoken_text(words, start: float, end: float) -> str:
    return " ".join(w.text for w in words if start <= w.start < end).strip()


def short_hook(text: str, fallback: str, max_chars: int = 60) -> str:
    """Erster Satz des Clips, sauber gekuerzt: bricht an Satz- oder Wortgrenze ab, nie mitten im Wort."""
    text = re.sub(r"\s+", " ", text).strip(" ,-")
    if not text:
        return fallback[:max_chars].strip()
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(sentence) <= max_chars:
        return sentence.rstrip(",;:-")
    cut = sentence[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def normalize_hashtags(tags, limit: int) -> list[str]:
    out = []
    for tag in tags:
        tag = re.sub(r"[^\w]", "", str(tag).lstrip("#"), flags=re.UNICODE)
        if tag and tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    return ["#" + t for t in out[:limit]]


# ---------------------------------------------------------------- Highlight-Auswahl
PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "viral_score": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "viral_score", "reason"],
            },
        },
    },
    "required": ["ranking"],
}


def pick_highlights(settings: dict, candidates: list[dict], count: int, video_title: str) -> list[dict]:
    """candidates: [{"start", "end", "score", "text"}]. Gibt die besten `count` zurueck (beste zuerst),
    mit "reason" und "viral_score", wenn die KI bewertet hat."""
    fallback = sorted(candidates, key=lambda c: c["score"], reverse=True)[:count]
    if not ai.enabled(settings) or len(candidates) <= 1:
        return fallback
    listing = "\n\n".join(
        f"[{i}] ({c['end'] - c['start']:.0f} s)\n{c['text'][:2500] or '(kaum Sprache)'}"
        for i, c in enumerate(candidates))
    prompt = (
        f"Video: {video_title}\n\n"
        f"Hier sind {len(candidates)} moegliche Clip-Ausschnitte als Transkript. "
        "Bewerte jeden mit viral_score von 0 bis 100: Wie wahrscheinlich geht er als Short viral? "
        "Achte besonders auf einen packenden Einstieg in den ersten Sekunden, Spannung, Humor oder Schock, "
        "eine Pointe vor dem Ende und ob man ihn ohne Kontext versteht. "
        "reason: ein kurzer Satz auf Deutsch. Gib alle Ausschnitte im Ranking zurueck, besten zuerst.\n\n"
        + listing
    )
    try:
        data = ai.generate_json(settings, prompt, PICK_SCHEMA, SYSTEM)
    except ai.AIError as exc:
        log.info("KI-Auswahl nicht moeglich (%s), nehme die lautesten Stellen.", exc)
        return fallback
    picked, seen = [], set()
    for entry in sorted(data["ranking"], key=lambda e: e["viral_score"], reverse=True):
        i = entry["index"]
        if 0 <= i < len(candidates) and i not in seen:
            seen.add(i)
            picked.append({**candidates[i], "viral_score": entry["viral_score"], "reason": entry["reason"]})
    return picked[:count] or fallback


# ---------------------------------------------------------------- Titel, Captions, Hashtags, Hook
TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook_overlay": {"type": "string"},
        "youtube_title": {"type": "string"},
        "youtube_description": {"type": "string"},
        "tiktok_caption": {"type": "string"},
        "instagram_caption": {"type": "string"},
        "hashtags_youtube": {"type": "array", "items": {"type": "string"}},
        "hashtags_tiktok": {"type": "array", "items": {"type": "string"}},
        "hashtags_instagram": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook_overlay", "youtube_title", "youtube_description", "tiktok_caption",
                 "instagram_caption", "hashtags_youtube", "hashtags_tiktok", "hashtags_instagram"],
}


def _template_texts(channel: dict, video_title: str, transcript: str, n: int) -> dict:
    hook = short_hook(transcript, video_title)
    fields = {"hook": hook, "video_title": video_title, "channel": channel["name"], "n": n}
    title = channel["title_template"].format(**fields).strip()[:100]
    caption = channel["caption_template"].format(**fields).strip()
    return {"title": title, "caption": caption, "hook_overlay": "", "platforms": {}}


def credit_line(channel: dict, video_title: str) -> str:
    credit = str(channel.get("credit") or "").strip()
    return f"Clip aus: {video_title}" + (f"\nCredit: {credit}" if credit else "")


def write_texts(settings: dict, channel: dict, video_title: str, transcript: str, n: int,
                reason: str = "") -> dict:
    """Gibt {"title", "caption", "hook_overlay", "platforms": {plattform: {"title", "caption"}}} zurueck.
    title/caption sind die allgemeine Fassung (Dashboard), platforms die Fassung je Plattform."""
    base = _template_texts(channel, video_title, transcript, n)
    if not ai.enabled(settings):
        return base
    language = channel.get("caption_language") or "Englisch"
    niche = ", ".join(channel.get("tags") or []) or channel["name"]
    prompt = (
        f"Kanal: {channel['name']} (Nische/Stichworte: {niche})\n"
        f"Quellvideo: {video_title}\n"
        + (f"Warum der Clip stark ist: {reason}\n" if reason else "")
        + f"\nTranskript des Clips:\n{transcript[:4000]}\n\n"
        f"Schreibe alle Texte auf {language}.\n"
        "- hook_overlay: 3 bis 7 Woerter, die in den ersten 3 Sekunden gross im Bild stehen und neugierig machen "
        "(z. B. 'Er hat nicht damit gerechnet…'). Kein Hashtag, kein Emoji-Spam.\n"
        "- youtube_title: hoechstens 70 Zeichen, Neugier-Luecke, kein Hashtag.\n"
        "- youtube_description: 1 bis 2 Saetze.\n"
        "- tiktok_caption: 1 kurzer Satz plus Frage oder Aufforderung zum Kommentieren, ohne Hashtags.\n"
        "- instagram_caption: starke erste Zeile, dann 1 bis 2 Saetze, ohne Hashtags.\n"
        "- hashtags_youtube: 3 bis 4 (shorts wird automatisch ergaenzt), hashtags_tiktok: 4 bis 6, "
        "hashtags_instagram: 6 bis 10. Mische 1 bis 2 breite Tags mit spezifischen Tags zur Person, "
        "zum Thema und zur Nische. Keine erfundenen oder irrefuehrenden Tags, keine Leerzeichen.\n"
    )
    try:
        data = ai.generate_json(settings, prompt, TEXT_SCHEMA, SYSTEM)
    except ai.AIError as exc:
        log.info("KI-Texte nicht moeglich (%s), nehme die Vorlagen.", exc)
        return base

    credit = credit_line(channel, video_title)
    own_tags = [t for t in (channel.get("tags") or [])]
    yt_tags = normalize_hashtags(data["hashtags_youtube"] + own_tags[:1] + ["shorts"], 5)
    tt_tags = normalize_hashtags(data["hashtags_tiktok"] + own_tags[:1], 6)
    ig_tags = normalize_hashtags(data["hashtags_instagram"] + own_tags[:1], 10)
    yt_title = data["youtube_title"].strip()[:90]
    if "#shorts" not in yt_title.lower():
        yt_title = f"{yt_title} #shorts"
    platforms = {
        "youtube": {"title": yt_title[:100],
                    "caption": f"{data['youtube_description'].strip()}\n\n{credit}\n\n{' '.join(yt_tags)}"},
        "tiktok": {"title": yt_title[:100],
                   "caption": f"{data['tiktok_caption'].strip()} {' '.join(tt_tags)}\n\n{credit}"},
        "instagram": {"title": yt_title[:100],
                      "caption": f"{data['instagram_caption'].strip()}\n\n{credit}\n\n{' '.join(ig_tags)}"},
    }
    return {"title": yt_title[:100], "caption": platforms["tiktok"]["caption"],
            "hook_overlay": data["hook_overlay"].strip()[:60], "platforms": platforms}
