"""Konfiguration laden (config.yaml + .env)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv ist optional
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent

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
}


class ConfigError(Exception):
    pass


NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _config_path(path=None) -> Path:
    return Path(path) if path else ROOT / "config.yaml"


def load_raw(path: str | Path | None = None) -> dict:
    """Konfiguration so, wie sie in der Datei steht (plus Standardwerte). Fehlt die Datei: leer."""
    if load_dotenv:
        load_dotenv(ROOT / ".env", override=True)
    path = _config_path(path)
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    settings = {**DEFAULTS, **(raw.get("settings") or {})}
    if settings["ai_backend"] is False:  # YAML liest ein ungequotetes off als false
        settings["ai_backend"] = "off"
    channels = raw.get("channels") or []
    for ch in channels:
        ch.setdefault("title_template", "{hook} #shorts")
        ch.setdefault("caption_template", "{hook}\n\nClip aus: {video_title}")
        ch.setdefault("tags", [])
        ch.setdefault("credit", "")
        ch.setdefault("caption_language", "Englisch")
        ch.setdefault("upload", {})
    return {"settings": settings, "channels": channels}


def validate(raw: dict, allow_empty: bool = False) -> None:
    settings, channels = raw["settings"], raw["channels"]
    if not channels and not allow_empty:
        raise ConfigError("In config.yaml ist kein Kanal unter 'channels' eingetragen.")
    names = set()
    for ch in channels:
        name = str(ch.get("name") or "")
        if not NAME_RE.match(name):
            raise ConfigError("Kanalname: nur Buchstaben, Ziffern, - und _ (max. 40 Zeichen).")
        if name in names:
            raise ConfigError(f"Kanalname '{name}' ist doppelt vergeben.")
        names.add(name)
        if not str(ch.get("source_url") or "").startswith("https://"):
            raise ConfigError(f"Kanal '{name}': Quell-Adresse muss mit https:// beginnen.")
    if settings["ai_backend"] not in ("off", "claude", "ollama"):
        raise ConfigError("KI: erlaubt sind off, claude oder ollama.")
    if settings["ai_effort"] not in ("low", "medium", "high"):
        raise ConfigError("KI-Aufwand: erlaubt sind low, medium oder high.")
    if settings["clip_min_seconds"] >= settings["clip_max_seconds"]:
        raise ConfigError("Die Mindestlaenge muss kleiner als die Hoechstlaenge sein.")


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
    data = {"settings": {k: raw["settings"][k] for k in DEFAULTS}, "channels": raw["channels"]}
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


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
