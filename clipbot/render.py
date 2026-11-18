"""Clip ausschneiden, auf 9:16 (1080x1920) bringen und Untertitel einbrennen (ffmpeg)."""
from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("clipbot")

W, H = 1080, 1920

# Homebrew baut "ffmpeg" inzwischen ohne libass (keine Untertitel). "brew install ffmpeg-full" legt eine
# vollstaendige Version daneben, die Homebrew nicht in den PATH haengt. Liegt sie da, nimmt ClipBot sie.
FULL_FFMPEG_DIRS = ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin")
SUBTITLE_HINT = ("Dein ffmpeg kann keine Untertitel einbrennen (libass fehlt). Clips entstehen trotzdem, aber "
                 "ohne Untertitel. Auf dem Mac im Terminal: brew install ffmpeg-full, danach ClipBot neu starten.")


def use_full_ffmpeg(dirs: tuple[str, ...] = FULL_FFMPEG_DIRS) -> str | None:
    """Stellt ein vorhandenes ffmpeg-full im PATH nach vorne. Gibt den Ordner zurueck (oder None)."""
    for folder in dirs:
        if Path(folder, "ffmpeg").exists():
            parts = os.environ.get("PATH", "").split(os.pathsep)
            if folder not in parts:
                os.environ["PATH"] = os.pathsep.join([folder, *parts])
            can_burn_subtitles.cache_clear()
            return folder
    return None


@functools.lru_cache(maxsize=1)
def can_burn_subtitles() -> bool:
    """True, wenn das ffmpeg im PATH den Filter "ass" hat (libass)."""
    if not shutil.which("ffmpeg"):
        return False
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return any(line.split()[1:2] == ["ass"] for line in out.stdout.splitlines() if line.strip())


_warned = False


def _subtitles_usable(ass_path: Path | None) -> bool:
    global _warned
    if not ass_path:
        return False
    if can_burn_subtitles():
        return True
    if not _warned:
        log.warning(SUBTITLE_HINT)
        _warned = True
    return False


def _filter(layout: str, zoom: float, ass_name: str | None) -> str:
    subs = f",ass={ass_name}" if ass_name else ""
    if layout == "crop":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"setsar=1{subs}[v]")
    fg_w = int(W * max(1.0, zoom)) // 2 * 2
    return (
        f"[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:4,eq=brightness=-0.12[bg];"
        f"[b]scale={fg_w}:-2,crop='min(iw,{W})':'min(ih,{H})'[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1{subs}[v]"
    )


def render_clip(source: Path, start: float, end: float, out_path: Path, ass_path: Path | None = None,
                layout: str = "blur", zoom: float = 1.4) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg laeuft im Ordner der Untertiteldatei: so braucht der Filter keinen Pfad
    # (Windows-Pfade mit "C:\" lassen sich im Filter kaum sauber maskieren).
    if not _subtitles_usable(ass_path):
        ass_path = None
    cwd = ass_path.parent if ass_path else out_path.parent
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(Path(source).resolve()),
        "-filter_complex", _filter(layout, zoom, ass_path.name if ass_path else None),
        "-map", "[v]", "-map", "0:a:0?",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg-Fehler: {result.stderr.strip()[-600:]}")
    return out_path


def still_seconds(source: Path, start: float = 0.0, end: float | None = None, min_still: float = 2.0) -> float:
    """Wie viele Sekunden des Abschnitts ein Standbild sind (Twitch-Werbepause "Commercial break in progress",
    Pausenbild, eingefrorener Stream). Gemessen mit dem ffmpeg-Filter freezedetect."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{max(0.0, start):.3f}"]
    if end is not None:
        cmd += ["-t", f"{max(0.1, end - start):.3f}"]
    cmd += ["-i", str(Path(source).resolve()), "-map", "0:v:0", "-vf", f"freezedetect=n=-50dB:d={min_still}",
            "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:
        return 0.0
    total, opened = 0.0, None
    for line in result.stderr.splitlines():
        if "freeze_start:" in line:
            opened = float(line.rsplit(":", 1)[1])
        elif "freeze_end:" in line and opened is not None:
            total += float(line.rsplit(":", 1)[1]) - opened
            opened = None
    if opened is not None:  # Standbild bis zum Schluss
        length = (end - start) if end is not None else None
        if length:
            total += max(0.0, length - opened)
    return total


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg-Fehler: {result.stderr.strip()[-600:]}")


def cut_raw(source: Path, start: float, end: float, out_path: Path) -> Path:
    """Schneidet einen Abschnitt bildgenau aus dem Quellvideo (Originalformat, hohe Qualitaet).
    Daraus entstehen spaeter die Best-of-Videos, auch wenn das Quellvideo schon geloescht ist."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-t", f"{end - start:.3f}",
          "-i", str(Path(source).resolve()), "-map", "0:v:0", "-map", "0:a:0?",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_path.resolve())])
    return out_path


def concat(parts: list[Path], out_path: Path) -> Path:
    """Haengt fertig gerenderte Abschnitte (gleiches Format) zu einem Video zusammen."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    listing = out_path.with_suffix(".txt")
    quoted = (Path(p).resolve().as_posix().replace("'", "'\\''") for p in parts)
    listing.write_text("".join(f"file '{q}'\n" for q in quoted), encoding="utf-8")
    base = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing.resolve())]
    try:
        try:  # ohne erneutes Kodieren: schnell und verlustfrei
            _run(base + ["-c", "copy", "-movflags", "+faststart", str(out_path.resolve())])
        except RuntimeError:  # Abschnitte passen nicht zusammen (z. B. einer ohne Ton): neu kodieren
            _run(base + ["-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
                         str(out_path.resolve())])
    finally:
        listing.unlink(missing_ok=True)
    return out_path
