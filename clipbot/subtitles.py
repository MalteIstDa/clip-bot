"""Untertitel im Shorts-Stil (wenige Woerter, aktuelles Wort hervorgehoben) als ASS-Datei."""
from __future__ import annotations

from pathlib import Path

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
HIGHLIGHT = r"{\c&H00F6FF&}"   # Gelb (ASS nutzt BGR)
RESET = r"{\c&HFFFFFF&}"


def _ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _clean(text: str, upper: bool) -> str:
    text = text.replace("{", "(").replace("}", ")").replace("\\", "").replace("\n", " ").strip()
    return text.upper() if upper else text


def _groups(words, per_line: int, max_chars: int = 20):
    group = []
    for w in words:
        too_long = sum(len(x.text) + 1 for x in group) + len(w.text) > max_chars
        pause = group and w.start - group[-1].end > 0.7
        sentence_end = group and group[-1].text.rstrip().endswith((".", "!", "?"))
        if group and (len(group) >= per_line or too_long or pause or sentence_end):
            yield group
            group = []
        group.append(w)
    if group:
        yield group


def write_ass(words, clip_start: float, clip_end: float, out_path: Path, font: str = "Arial",
              size: int = 86, margin_bottom: int = 430, uppercase: bool = True, per_line: int = 3) -> Path:
    """`words` haben Zeiten relativ zum Quellvideo; die Datei nutzt Zeiten relativ zum Clip."""
    inside = [w for w in words if w.end > clip_start and w.start < clip_end]
    lines = [HEADER.format(font=font, size=size, margin=margin_bottom)]
    duration = clip_end - clip_start
    groups = list(_groups(inside, per_line))
    for g, group in enumerate(groups):
        next_start = groups[g + 1][0].start if g + 1 < len(groups) else float("inf")
        texts = [_clean(w.text, uppercase) for w in group]
        for i, w in enumerate(group):
            begin = max(0.0, w.start - clip_start)
            nxt = group[i + 1].start if i + 1 < len(group) else min(w.end + 0.35, next_start)
            finish = min(duration, max(nxt - clip_start, begin + 0.05))
            parts = [f"{HIGHLIGHT}{t}{RESET}" if j == i else t for j, t in enumerate(texts)]
            lines.append(f"Dialogue: 0,{_ts(begin)},{_ts(finish)},Cap,,0,0,0,,{' '.join(parts)}\n")
    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
