"""Sprecherstimme fuer Erklaervideos: ElevenLabs mit Zeitstempeln je Zeichen -> Woerter mit Zeiten.

Ohne Schluessel gibt es den Modus "stumm": eine stille Tonspur mit geschaetzten Wortzeiten,
um Layout und Animation kostenlos zu pruefen.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class VoiceError(Exception):
    pass


@dataclass
class Word:
    start: float
    end: float
    text: str


def full_text(scenes: list[dict]) -> tuple[str, list[tuple[int, int]]]:
    """Alle Szenen als ein Text; dazu je Szene der Zeichenbereich [von, bis)."""
    parts, ranges, pos = [], [], 0
    for scene in scenes:
        text = re.sub(r"\s+", " ", scene["voice"]).strip()
        ranges.append((pos, pos + len(text)))
        parts.append(text)
        pos += len(text) + 1  # Leerzeichen dazwischen
    return " ".join(parts), ranges


def words_from_alignment(text: str, starts: list[float], ends: list[float]) -> list[tuple[int, Word]]:
    """Zeichen-Zeitstempel zu Woertern zusammenfassen. Gibt (Zeichenposition, Word) zurueck."""
    words, cur, cur_start, cur_pos, last_end = [], "", 0.0, 0, 0.0
    for i, ch in enumerate(text):
        if ch.isspace():
            if cur:
                words.append((cur_pos, Word(cur_start, last_end, cur)))
                cur = ""
            continue
        if not cur:
            cur_start, cur_pos = starts[i], i
        cur += ch
        last_end = ends[i]
    if cur:
        words.append((cur_pos, Word(cur_start, last_end, cur)))
    return words


def scene_times(words: list[tuple[int, Word]], ranges: list[tuple[int, int]], total: float) -> list[tuple[float, float]]:
    """Start und Ende jeder Szene: ab ihrem ersten Wort bis zum ersten Wort der naechsten Szene."""
    firsts = []
    for a, b in ranges:
        inside = [w for pos, w in words if a <= pos < b]
        firsts.append(inside[0].start if inside else (firsts[-1] if firsts else 0.0))
    firsts[0] = 0.0
    return [(firsts[i], firsts[i + 1] if i + 1 < len(firsts) else total) for i in range(len(firsts))]


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError as exc:
        raise VoiceError(f"Tonspur unlesbar: {path}") from exc


def elevenlabs(text: str, out: Path, voice_id: str, model_id: str) -> tuple[list[float], list[float]]:
    import requests
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY fehlt (in .env eintragen).")
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": model_id, "language_code": "de"},
            timeout=180,
        )
    except requests.RequestException as exc:
        raise VoiceError(f"ElevenLabs nicht erreichbar: {exc}") from exc
    if r.status_code == 401:
        raise VoiceError("ElevenLabs: API-Schluessel ungueltig.")
    if not r.ok:
        raise VoiceError(f"ElevenLabs: Fehler {r.status_code}: {r.text[:300]}")
    data = r.json()
    out.write_bytes(base64.b64decode(data["audio_base64"]))
    align = data.get("alignment") or data.get("normalized_alignment") or {}
    starts, ends = align.get("character_start_times_seconds"), align.get("character_end_times_seconds")
    if not starts or len(starts) != len(text):
        raise VoiceError("ElevenLabs hat keine passenden Zeitstempel geliefert.")
    return starts, ends


def silent(text: str, out: Path, seconds_per_word: float = 0.36) -> tuple[list[float], list[float]]:
    """Stille Tonspur mit geschaetzten Zeiten (Vorschau ohne Stimme)."""
    starts, ends, t = [], [], 0.25
    for token in re.split(r"(\s+)", text):
        if not token:
            continue
        if token.isspace():
            starts += [t] * len(token)
            ends += [t] * len(token)
            continue
        per_char = (seconds_per_word + (0.25 if token[-1] in ".!?" else 0)) / len(token)
        for _ in token:
            starts.append(t)
            t += per_char
            ends.append(t)
    total = t + 0.6
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", f"{total:.2f}", "-c:a", "libmp3lame", "-b:a", "64k", str(out)], check=True)
    return starts, ends


def speak(settings: dict, scenes: list[dict], out_dir: Path) -> dict:
    """Erzeugt assets/voice.mp3. Gibt {"audio", "duration", "words", "scenes": [(start, end)]} zurueck."""
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = out_dir / "voice.mp3"
    text, ranges = full_text(scenes)
    if settings["voice_backend"] == "elevenlabs":
        starts, ends = elevenlabs(text, audio, settings["elevenlabs_voice_id"], settings["elevenlabs_model"])
    else:
        starts, ends = silent(text, audio)
    words = words_from_alignment(text, starts, ends)
    duration = max(_duration(audio), words[-1][1].end + 0.2 if words else 0)
    return {"audio": audio, "duration": duration, "words": [w for _, w in words],
            "scenes": scene_times(words, ranges, duration)}
