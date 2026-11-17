"""Neueste Videos eines Kanals finden und herunterladen (yt-dlp)."""
from __future__ import annotations

from pathlib import Path

from clipbot import sources

MIN_SECONDS = 150  # kuerzere Videos (z. B. Shorts, Trailer) lohnen sich nicht zum Clippen


class Skipped(Exception):
    """Das Video wird bewusst nicht verarbeitet; der Text nennt den Grund."""


def list_latest(source_url: str, limit: int) -> list[dict]:
    """Die neuesten Videos eines Kanals (YouTube, Twitch-Aufzeichnungen, ...), neueste zuerst."""
    return sources.list_latest(source_url, limit)


def download(video: dict, out_dir: Path, max_height: int, max_minutes: int) -> tuple[Path, dict]:
    """Laedt ein Video herunter und gibt (Pfad, Infos) zurueck. Loest Skipped aus, wenn es nicht passt."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    h = max_height
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video["url"], download=False)
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise Skipped("laeuft gerade live oder ist erst angekuendigt")
        duration = info.get("duration") or 0
        if not duration:
            raise Skipped("die Laenge ist unbekannt")
        if duration < MIN_SECONDS:
            raise Skipped(f"nur {duration:.0f} Sekunden lang, noetig sind mindestens {MIN_SECONDS}")
        if duration > max_minutes * 60:
            raise Skipped(f"{duration / 60:.0f} Minuten lang, erlaubt sind hoechstens {max_minutes} (Einstellungen)")
        ydl.process_info(info)
    path = out_dir / f"{info['id']}.mp4"
    if not path.exists():  # anderes Containerformat
        found = [p for p in out_dir.glob(f"{info['id']}.*") if p.suffix != ".part"]
        if not found:
            raise RuntimeError(f"Download von {video['id']} fehlgeschlagen")
        path = found[0]
    return path, {
        "id": info["id"],
        "title": info.get("title") or video["title"],
        "duration": float(duration),
        "uploader": info.get("channel") or info.get("uploader") or "",
        "heatmap": info.get("heatmap") or [],  # "Most replayed"-Daten, falls vorhanden
        # fuer die Art des Videos (Gaming, Just Chatting, IRL): YouTube-Kategorie, Twitch-Kapitel
        "categories": info.get("categories") or [],
        "chapters": info.get("chapters") or [],
        "extractor_key": info.get("extractor_key") or "",
    }
