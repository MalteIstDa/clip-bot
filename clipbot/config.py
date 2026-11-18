"""Konfiguration laden (config.yaml + .env)."""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv ist optional
    load_dotenv = None

from clipbot import lang, schedule

ROOT = Path(__file__).resolve().parent.parent
LOCK = threading.RLock()  # schuetzt config.yaml vor gleichzeitigen Aenderungen aus dem Dashboard
PLATFORMS = ("youtube", "tiktok", "instagram")

DEFAULTS = {
    "dry_run": True,
    "work_dir": "data",
    "videos_per_run": 2,
    "max_video_minutes": 300,
    "max_height": 1080,
    "clips_per_video": 3,
    "clip_min_seconds": 60,
    "clip_max_seconds": 120,
    "skip_start_seconds": 60,
    "skip_end_seconds": 30,
    "max_uploads_per_run": 6,
    "keep_source_videos": False,
    "whisper_model": "small",
    "language": None,
    "layout": "blur",
    # Format je Art des Streams (Kategorie bei Twitch/YouTube)
    "genre_formats": True,             # Gaming, Just Chatting und IRL erkennen und Bild, Hook und Hashtags anpassen
    "layout_gaming": "blur",           # ganzes Spielbild sichtbar
    "layout_chatting": "crop",         # bildfuellend auf den Streamer
    "layout_irl": "crop",
    "zoom": 1.15,
    "subtitle_font": "Arial",
    "subtitle_size": 86,
    "subtitle_margin_bottom": 430,
    "subtitle_uppercase": True,
    "subtitle_words_per_line": 3,
    "youtube_category_id": "24",
    "privacy": "public",
    "instagram_api_version": "v26.0",
    "require_approval": True,          # neue Clips erst nach Freigabe im Dashboard hochladen
    "ai_candidates": 6,                # so viele Stellen bewertet die KI je Video
    "hook_overlay": True,              # Hook-Zeile in den ersten Sekunden einblenden
    "ai_backend": "off",               # off | claude | ollama
    "ai_claude_model": "claude-opus-5-5",
    "ai_effort": "low",                # low | medium | high (nur Claude)
    "ai_ollama_model": "qwen3:8b",
    "ai_ollama_url": "http://localhost:11434",
    # Erklaervideos
    "voice_backend": "elevenlabs",     # elevenlabs | stumm (Vorschau ohne Stimme, kostenlos)
    "elevenlabs_voice_id": "JBFqnCBsd6RMkjVDRZzb",
    "elevenlabs_model": "eleven_multilingual_v2",
    "hyperframes_cmd": "npx --yes hyperframes@0.9.18",
    "explainer_queries": "was passiert wenn, menschlicher körper fakten, geschichte erklärt, politik einfach erklärt, ki erklärt",
    "explainer_min_seconds": 65,  # ueber 1 Minute: Voraussetzung fuer Verguetung (z. B. TikTok Creator Rewards)
    "explainer_max_seconds": 90,
    "explainer_themes": "menschlicher Körper, Was passiert wenn, Geschichte, Politik, Wissenschaft, KI und Technik",
    # Creator-Tag oben im Bild
    "creator_tag": True,               # die Zeile oben im Clip nennt den Creator beim Namen
    "hook_seconds": 0.0,               # so lange bleibt die Zeile stehen; 0 = den ganzen Clip
    # Automatik (Watchlist, automatisches Clippen, Zeitplan)
    "autopilot": False,                # neue Videos automatisch clippen, Best-ofs bauen, zu den Uhrzeiten hochladen
    "watch_interval_minutes": 5,       # so oft wird die Watchlist geprueft
    "upload_times": "",                # z. B. "12:00, 17:30, 20:00"; leer = kein automatischer Upload
    "upload_grace_minutes": 60,        # so lange nach einer Uhrzeit wird ein verpasster Upload noch nachgeholt
    # Best-of-Videos
    "moments_per_video": 3,            # so viele kurze Momente je Video fuer Best-ofs merken
    "moment_min_seconds": 8,
    "moment_max_seconds": 22,
    "bestof_min_seconds": 60,
    "bestof_max_seconds": 120,
    "bestof_max_per_creator": 2,       # hoechstens so viele Momente je Creator in einem Best-of
    "bestof_time": "18:00",            # ab dieser Uhrzeit baut die Automatik das Best-of
    "bestof_weekday": 6,               # Wochentag fuer woechentliche Best-ofs: 0 = Montag ... 6 = Sonntag
    # Live-Clips aus Chat-Peaks und Vault
    "live_clips": True,                # laufende Streams der Watchlist mitschneiden und bei Chat-Peaks clippen
    "live_max_streams": 6,             # so viele Streams hoechstens gleichzeitig mitschneiden
    "live_sensitivity": "normal",      # niedrig | normal | hoch: wie stark der Chat ausrasten muss
    "live_before_seconds": 40,         # so viel vor dem Chat-Peak beginnt der Clip
    "live_after_seconds": 12,          # so viel nach dem Beginn des Peaks endet er
    "live_cooldown_seconds": 120,      # Mindestabstand zwischen zwei Clips aus demselben Stream
    "live_max_clips_per_stream": 12,   # bei Zusammenschnitten: so viele Zusammenschnitte je Stream
    "live_compilation": True,          # Peaks als kurze Momente sammeln und zu einem Clip ueber 1 Minute zusammensetzen
    "live_moment_seconds": 20,         # so lang ist ein Moment (der Kern um den Chat-Peak)
    "live_compilation_min_seconds": 65,  # TikTok zahlt erst ab ueber 1 Minute
    "live_compilation_max_seconds": 90,
    "vault_size": 10,                  # sind so viele Live-Clips eines Kanals im Vault, wird gepostet; 0 = nie automatisch
    # Benachrichtigungen
    "notify_desktop": True,            # Mitteilung des Betriebssystems (Mac, Windows)
    "notify_ntfy_topic": "",           # Handy: Thema bei ntfy.sh (App "ntfy" installieren und das Thema abonnieren)
}


class ConfigError(Exception):
    pass


NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _config_path(path=None) -> Path:
    return Path(path) if path else ROOT / "config.yaml"


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"})


def slug(text: str) -> str:
    """Freien Namen ("Deutsche Größen") in einen Kurznamen ("deutsche-groessen") umwandeln."""
    text = str(text or "").translate(_UMLAUTS).lower()
    return re.sub(r"[^a-z0-9_-]+", "-", text).strip("-_")[:40].strip("-_")


def _unique(base: str, taken) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")[:36] or "eintrag"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def _same_url(a: str, b: str) -> bool:
    return str(a or "").rstrip("/").lower() == str(b or "").rstrip("/").lower()


def _migrate_channel(ch: dict, accounts: list, creators: list) -> None:
    """Alte Kanaele (eine Quelle, je Plattform ein Konto) auf Creator- und Konten-Listen umstellen."""
    old_upload = ch.pop("upload", None) or {}
    if "accounts" not in ch:
        ids = []
        for platform, target in old_upload.items():
            acc_id = f"{ch['name']}_{platform}"
            if not any(a.get("id") == acc_id for a in accounts):
                accounts.append({"id": acc_id, "platform": platform, "label": ch["name"], **(target or {})})
            ids.append(acc_id)
        ch["accounts"] = ids
    source = str(ch.pop("source_url", "") or "").strip()
    if "creators" not in ch:
        ch["creators"] = []
        if source:
            known = next((c for c in creators if _same_url(c.get("url"), source)), None)
            if not known:
                known = {"id": _unique(ch["name"], {c.get("id") for c in creators}),
                         "name": str(ch.get("credit") or ch["name"]), "url": source,
                         "notify": True, "auto_clip": True}
                creators.append(known)
            ch["creators"] = [known["id"]]
    old_language = ch.pop("caption_language", None)
    default = "de" if ch.get("kind") == "explainer" else "en"
    ch["language"] = lang.code(ch.get("language") or old_language, default)


def load_raw(path: str | Path | None = None) -> dict:
    """Konfiguration so, wie sie in der Datei steht (plus Standardwerte). Fehlt die Datei: leer."""
    if load_dotenv:
        load_dotenv(ROOT / ".env", override=True)
    path = _config_path(path)
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    settings = {**DEFAULTS, **(raw.get("settings") or {})}
    if settings["ai_backend"] is False:  # YAML liest ein ungequotetes off als false
        settings["ai_backend"] = "off"
    accounts = [dict(a) for a in raw.get("accounts") or []]
    creators = [dict(c) for c in raw.get("creators") or []]
    channels = raw.get("channels") or []
    for ch in channels:
        ch.setdefault("title_template", "{hook} #shorts")
        ch.setdefault("caption_template", "{hook}\n\nClip aus: {video_title}")
        ch.setdefault("tags", [])
        ch.setdefault("credit", "")
        ch.setdefault("kind", "clips")  # clips = Clips aus Videos der Creator | explainer = eigene Erklaervideos
        _migrate_channel(ch, accounts, creators)
        ch.setdefault("clips", True)        # einzelne Clips je Video erzeugen
        if ch.get("bestof") in (None, False):  # YAML liest ein ungequotetes off als false
            ch["bestof"] = "off"            # off | daily | weekly: Best-of-Videos aus mehreren Creatorn
        ch.setdefault("distribute", "alle")  # alle = jedes Konto bekommt jeden Clip | abwechselnd = verteilen
    for acc in accounts:
        acc.setdefault("label", acc.get("id", ""))
        acc.setdefault("times", "")
    for cr in creators:
        cr.setdefault("name", cr.get("id", ""))
        cr.setdefault("notify", True)
        cr.setdefault("auto_clip", True)
    return {"settings": settings, "accounts": accounts, "creators": creators, "channels": channels}


def _check_times(text, what: str) -> None:
    try:
        schedule.parse_times(text)
    except ValueError as exc:
        raise ConfigError(f"{what}: {exc}") from exc


def validate(raw: dict, allow_empty: bool = False) -> None:
    settings, channels = raw["settings"], raw["channels"]
    accounts, creators = raw.get("accounts") or [], raw.get("creators") or []
    if not channels and not allow_empty:
        raise ConfigError("In config.yaml ist kein Kanal unter 'channels' eingetragen.")
    account_ids, creator_ids = set(), set()
    for acc in accounts:
        acc_id = str(acc.get("id") or "")
        if not NAME_RE.match(acc_id):
            raise ConfigError("Konto: Der Kurzname darf nur Buchstaben, Ziffern, - und _ enthalten (max. 40 Zeichen).")
        if acc_id in account_ids:
            raise ConfigError(f"Konto '{acc_id}' ist doppelt vergeben.")
        account_ids.add(acc_id)
        if acc.get("platform") not in PLATFORMS:
            raise ConfigError(f"Konto '{acc_id}': Plattform muss youtube, tiktok oder instagram sein.")
        _check_times(acc.get("times"), f"Konto '{acc_id}', Upload-Zeiten")
    for cr in creators:
        cr_id = str(cr.get("id") or "")
        if not NAME_RE.match(cr_id):
            raise ConfigError("Creator: Der Kurzname darf nur Buchstaben, Ziffern, - und _ enthalten (max. 40 Zeichen).")
        if cr_id in creator_ids:
            raise ConfigError(f"Creator '{cr_id}' ist doppelt vergeben.")
        creator_ids.add(cr_id)
        if not str(cr.get("url") or "").startswith("https://"):
            raise ConfigError(f"Creator '{cr_id}': Die Adresse muss mit https:// beginnen.")
    names = set()
    for ch in channels:
        name = str(ch.get("name") or "")
        if not NAME_RE.match(name):
            raise ConfigError("Kanalname: nur Buchstaben, Ziffern, - und _ (max. 40 Zeichen).")
        if name in names:
            raise ConfigError(f"Kanalname '{name}' ist doppelt vergeben.")
        names.add(name)
        if ch.get("kind", "clips") not in ("clips", "explainer"):
            raise ConfigError(f"Kanal '{name}': Art muss clips oder explainer sein.")
        if ch.get("bestof", "off") not in ("off", "daily", "weekly"):
            raise ConfigError(f"Kanal '{name}': Best-of muss off, daily oder weekly sein.")
        if ch.get("distribute", "alle") not in ("alle", "abwechselnd"):
            raise ConfigError(f"Kanal '{name}': Verteilung muss alle oder abwechselnd sein.")
        for cr_id in ch.get("creators") or []:
            if cr_id not in creator_ids:
                raise ConfigError(f"Kanal '{name}': Den Creator '{cr_id}' gibt es nicht in der Watchlist.")
        for acc_id in ch.get("accounts") or []:
            if acc_id not in account_ids:
                raise ConfigError(f"Kanal '{name}': Das Konto '{acc_id}' gibt es nicht.")
    if settings["ai_backend"] not in ("off", "claude", "ollama"):
        raise ConfigError("KI: erlaubt sind off, claude oder ollama.")
    if settings["ai_effort"] not in ("low", "medium", "high"):
        raise ConfigError("KI-Aufwand: erlaubt sind low, medium oder high.")
    if settings["voice_backend"] not in ("elevenlabs", "stumm"):
        raise ConfigError("Stimme: erlaubt sind elevenlabs oder stumm.")
    if not 15 <= settings["explainer_min_seconds"] < settings["explainer_max_seconds"] <= 180:
        raise ConfigError("Erklaervideos: Mindestlaenge muss kleiner als die Hoechstlaenge sein (15 bis 180 Sekunden).")
    if settings["clip_min_seconds"] >= settings["clip_max_seconds"]:
        raise ConfigError("Die Mindestlaenge muss kleiner als die Hoechstlaenge sein.")
    if not 3 <= settings["moment_min_seconds"] < settings["moment_max_seconds"] <= 60:
        raise ConfigError("Best-of-Momente: Mindestlaenge muss kleiner als die Hoechstlaenge sein (3 bis 60 Sekunden).")
    if not 15 <= settings["bestof_min_seconds"] < settings["bestof_max_seconds"] <= 600:
        raise ConfigError("Best-of: Mindestlaenge muss kleiner als die Hoechstlaenge sein (15 bis 600 Sekunden).")
    if not 0 <= int(settings["bestof_weekday"]) <= 6:
        raise ConfigError("Best-of-Wochentag: 0 (Montag) bis 6 (Sonntag).")
    for key in ("layout", "layout_gaming", "layout_chatting", "layout_irl"):
        if settings[key] not in ("blur", "crop"):
            raise ConfigError("Bildaufbau: erlaubt sind blur oder crop.")
    if settings["live_sensitivity"] not in ("niedrig", "normal", "hoch"):
        raise ConfigError("Live-Empfindlichkeit: erlaubt sind niedrig, normal oder hoch.")
    if not 10 <= settings["live_before_seconds"] + settings["live_after_seconds"] <= 180:
        raise ConfigError("Live-Clips: Vorlauf und Nachlauf zusammen muessen zwischen 10 und 180 Sekunden liegen.")
    if not 6 <= settings["live_moment_seconds"] <= 60:
        raise ConfigError("Live-Momente: 6 bis 60 Sekunden.")
    if not 20 <= settings["live_compilation_min_seconds"] < settings["live_compilation_max_seconds"] <= 600:
        raise ConfigError("Live-Zusammenschnitt: Mindestlaenge muss kleiner als die Hoechstlaenge sein (20 bis 600 Sekunden).")
    if settings["live_max_streams"] < 1 or settings["vault_size"] < 0:
        raise ConfigError("Live-Clips: mindestens 1 Stream gleichzeitig, Vault-Groesse 0 oder mehr.")
    if settings["watch_interval_minutes"] < 1:
        raise ConfigError("Die Watchlist kann hoechstens einmal pro Minute geprueft werden.")
    _check_times(settings["upload_times"], "Upload-Zeiten")
    _check_times(settings["bestof_time"], "Best-of-Uhrzeit")
    if not re.fullmatch(r"[A-Za-z0-9_-]{0,64}", str(settings["notify_ntfy_topic"] or "")):
        raise ConfigError("ntfy-Thema: nur Buchstaben, Ziffern, - und _ (max. 64 Zeichen).")


def load(path: str | Path | None = None, allow_empty: bool = False) -> dict:
    if not _config_path(path).exists() and not allow_empty:
        raise ConfigError(
            "config.yaml fehlt. Kopiere config.example.yaml nach config.yaml und passe sie an."
        )
    raw = load_raw(path)
    validate(raw, allow_empty)
    work = Path(raw["settings"]["work_dir"])
    raw["settings"]["work_dir"] = work if work.is_absolute() else ROOT / work
    return raw


def save(raw: dict, path: str | Path | None = None) -> None:
    """Schreibt config.yaml neu (Kommentare gehen verloren; die erste Fassung bleibt als .bak erhalten)."""
    validate(raw, allow_empty=True)
    path = _config_path(path)
    backup = path.with_suffix(".yaml.bak")
    if path.exists() and not backup.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    old_format = path.with_suffix(".yaml.vor-konten.bak")  # Stand vor der Umstellung auf Konten und Watchlist
    if path.exists() and not old_format.exists():
        text = path.read_text(encoding="utf-8")
        if "accounts" not in (yaml.safe_load(text) or {}):
            old_format.write_text(text, encoding="utf-8")
    data = {"settings": {k: raw["settings"][k] for k in DEFAULTS}, "accounts": raw.get("accounts") or [],
            "creators": raw.get("creators") or [], "channels": raw["channels"]}
    with LOCK:
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------- Verknuepfungen Kanal <-> Creator <-> Konto
def accounts_of(cfg: dict, channel: dict) -> list[dict]:
    by_id = {a["id"]: a for a in cfg.get("accounts") or []}
    return [by_id[i] for i in channel.get("accounts") or [] if i in by_id]


def creators_of(cfg: dict, channel: dict) -> list[dict]:
    by_id = {c["id"]: c for c in cfg.get("creators") or []}
    return [by_id[i] for i in channel.get("creators") or [] if i in by_id]


def channels_of_creator(cfg: dict, creator_id: str) -> list[dict]:
    return [ch for ch in cfg["channels"] if ch.get("kind", "clips") == "clips" and creator_id in (ch.get("creators") or [])]


def channels_of_account(cfg: dict, account_id: str) -> list[dict]:
    return [ch for ch in cfg["channels"] if account_id in (ch.get("accounts") or [])]


def account_connected(account: dict) -> bool:
    """Ist das Konto angemeldet (Token vorhanden)?"""
    if account.get("platform") == "instagram":
        return bool(account.get("ig_user_id") and os.environ.get(str(account.get("token_env") or "")))
    return bool(account.get("token_file")) and resolve(account["token_file"]).exists()


def resolve(path: str | Path) -> Path:
    """Relative Pfade (Token-Dateien usw.) relativ zum Projektordner aufloesen."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def youtube_secrets_file() -> Path:
    """OAuth-Client der eigenen Google-App. Ohne Eintrag in .env gilt client_secret.json im Projektordner."""
    return resolve(os.environ.get("YOUTUBE_CLIENT_SECRETS") or "client_secret.json")


def env(name: str, required: bool = True) -> str | None:
    value = os.environ.get(name)
    if required and not value:
        raise ConfigError(f"Umgebungsvariable {name} fehlt (in .env eintragen).")
    return value
