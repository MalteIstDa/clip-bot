"""Clip ausschneiden, auf 9:16 (1080x1920) bringen und Untertitel einbrennen (ffmpeg)."""
from __future__ import annotations

import subprocess
from pathlib import Path

W, H = 1080, 1920


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
