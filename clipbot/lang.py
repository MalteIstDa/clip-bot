"""Sprachen fuer Texte im Video und in den Captions: Deutsch, Englisch (US), Franzoesisch, Polnisch."""
from __future__ import annotations

LANGS = {
    "de": {
        "label": "Deutsch", "prompt": "Deutsch", "code": "de",
        "clip_from": "Clip aus: {title}", "credit": "Credit: {name}", "credits": "Credits: {names}",
        "bestof_daily": "Best of des Tages", "bestof_weekly": "Best of der Woche", "and_more": "und mehr",
        "bestof_caption": "Die besten Momente auf einen Blick. Welcher war dein Favorit?",
        "bestof_tags": ["bestof", "clips", "streamer", "fyp"],
    },
    "en": {
        "label": "Englisch (US)", "prompt": "amerikanischem Englisch", "code": "en",
        "clip_from": "Clip from: {title}", "credit": "Credit: {name}", "credits": "Credits: {names}",
        "bestof_daily": "Best of the Day", "bestof_weekly": "Best of the Week", "and_more": "and more",
        "bestof_caption": "The best moments in one video. Which one was your favorite?",
        "bestof_tags": ["bestof", "clips", "streamer", "fyp"],
    },
    "fr": {
        "label": "Französisch", "prompt": "Französisch", "code": "fr",
        "clip_from": "Extrait de : {title}", "credit": "Crédit : {name}", "credits": "Crédits : {names}",
        "bestof_daily": "Best of du jour", "bestof_weekly": "Best of de la semaine", "and_more": "et plus",
        "bestof_caption": "Les meilleurs moments en une vidéo. Lequel est ton préféré ?",
        "bestof_tags": ["bestof", "clips", "streamer", "pourtoi"],
    },
    "pl": {
        "label": "Polnisch", "prompt": "Polnisch", "code": "pl",
        "clip_from": "Klip z: {title}", "credit": "Źródło: {name}", "credits": "Źródła: {names}",
        "bestof_daily": "Najlepsze momenty dnia", "bestof_weekly": "Najlepsze momenty tygodnia", "and_more": "i inni",
        "bestof_caption": "Najlepsze momenty w jednym filmie. Który był twoim ulubionym?",
        "bestof_tags": ["bestof", "klipy", "streamer", "dlaciebie"],
    },
}

_ALIASES = {
    "deutsch": "de", "german": "de", "englisch": "en", "english": "en", "amerikanisch": "en",
    "englisch (us)": "en", "en-us": "en", "französisch": "fr", "franzoesisch": "fr", "french": "fr",
    "français": "fr", "polnisch": "pl", "polish": "pl", "polski": "pl",
}


def code(value, default: str = "en") -> str:
    """Kuerzel der Sprache ('de', 'en', 'fr', 'pl'). Unbekannte Angaben (z. B. 'Spanisch') bleiben, wie sie sind."""
    text = str(value or "").strip()
    if not text:
        return default
    low = text.lower()
    if low in LANGS:
        return low
    return _ALIASES.get(low, text)


def get(value, default: str = "en") -> dict:
    """Textbausteine der Sprache. Unbekannte Sprachen bekommen englische Bausteine und ihren Namen fuer die KI."""
    key = code(value, default)
    if key in LANGS:
        return LANGS[key]
    return {**LANGS["en"], "label": key, "prompt": key, "code": ""}


def of_channel(channel: dict) -> dict:
    default = "de" if channel.get("kind") == "explainer" else "en"
    return get(channel.get("language") or channel.get("caption_language"), default)


def labels() -> dict:
    return {key: entry["label"] for key, entry in LANGS.items()}
