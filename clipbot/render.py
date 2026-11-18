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


CAM_H = 640  # Hoehe der Facecam oben (Layout "facecam"); darunter das Spiel
PUNCH = 1.16  # so stark zoomt ein Punch-in


def _punch(punches) -> str:
    """Kurze Zooms (Punch-ins) an lauten Stellen. punches: [(von, bis)] in Sekunden ab Clipbeginn."""
    if not punches:
        return ""
    cond = "+".join(f"between(in_time,{a:.2f},{b:.2f})" for a, b in punches)
    return (f",zoompan=z='if({cond},{PUNCH},1)':d=1:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
            f":s={W}x{H}:fps=30")


def _filter(layout: str, zoom: float, ass_name: str | None, cam=None, punches=()) -> str:
    subs = f",ass={ass_name}" if ass_name else ""
    after = f"{_punch(punches)},setsar=1{subs}[v]"
    if layout == "facecam" and cam:
        x, y, w, h = cam
        game_h = H - CAM_H
        return (f"[0:v]split[a][b];[a]crop={w}:{h}:{x}:{y},scale={W}:{CAM_H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{CAM_H}[top];[b]scale=-2:{game_h},crop='min(iw,{W})':{game_h},"
                f"pad={W}:{game_h}:(ow-iw)/2:0[bot];[top][bot]vstack{after}")
    if layout == "crop":
        return f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}{after}"
    fg_w = int(W * max(1.0, zoom)) // 2 * 2
    return (
        f"[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:4,eq=brightness=-0.12[bg];"
        f"[b]scale={fg_w}:-2,crop='min(iw,{W})':'min(ih,{H})'[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2{after}"
    )


HOOK_MARGIN = {"facecam": CAM_H - 50}  # Hook-Kasten auf der Kante zwischen Facecam und Spiel, nicht ueber dem Gesicht


def prepare_layout(source: Path, start: float, end: float, layout: str):
    """Fuer "facecam": die Facecam suchen. Gibt (layout, cam) zurueck; ohne Facecam ("blur", None)."""
    if layout != "facecam":
        return layout, None
    from clipbot import facecam
    cam = facecam.find(Path(source), start, end)
    if not cam:
        log.info("Keine Facecam gefunden, der Clip zeigt das ganze Bild.")
        return "blur", None
    return "facecam", cam


def loud_spots(source: Path, start: float, end: float, count: int = 3, length: float = 1.2) -> list[tuple[float, float]]:
    """Die lautesten Stellen des Abschnitts (Schreien, Lachen, Explosion) fuer Punch-ins,
    mindestens 4 Sekunden auseinander. Zeiten ab Beginn des Abschnitts."""
    import numpy as np
    from clipbot import highlights

    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{end - start:.3f}",
           "-i", str(Path(source).resolve()), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=120).stdout
    except Exception:
        return []
    step = 4000  # halbe Sekunde bei 8 kHz
    samples = np.frombuffer(raw[: len(raw) - len(raw) % (step * 2)], dtype=np.int16).astype(np.float32)
    if len(samples) < step * 4:
        return []
    rms = np.sqrt((samples.reshape(-1, step) ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1.0) / 32768.0)
    z = highlights._zscore(db)
    spots: list[float] = []
    for i in np.argsort(-z):
        t = i * 0.5
        if z[i] < 1.5 or len(spots) >= count:
            break
        if t < 1.0 or t > (end - start) - length:
            continue
        if all(abs(t - s) >= 4 for s in spots):
            spots.append(t)
    return sorted((max(0.0, t - 0.2), t - 0.2 + length) for t in spots)


def render_clip(source: Path, start: float, end: float, out_path: Path, ass_path: Path | None = None,
                layout: str = "blur", zoom: float = 1.4, punch_ins: bool = False, cam=None) -> Path:
    """layout: blur (ganzes Bild auf Unschaerfe), crop (bildfuellend) oder facecam (Facecam oben, Spiel unten;
    ohne erkennbare Facecam wie blur). punch_ins: an den lautesten Stellen kurz ins Bild zoomen."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if layout == "facecam" and not cam:
        layout, cam = prepare_layout(source, start, end, layout)
    punches = loud_spots(source, start, end) if punch_ins else []
    # ffmpeg laeuft im Ordner der Untertiteldatei: so braucht der Filter keinen Pfad
    # (Windows-Pfade mit "C:\" lassen sich im Filter kaum sauber maskieren).
    if not _subtitles_usable(ass_path):
        ass_path = None
    cwd = ass_path.parent if ass_path else out_path.parent
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(Path(source).resolve()),
        "-filter_complex", _filter(layout, zoom, ass_path.name if ass_path else None, cam, punches),
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
