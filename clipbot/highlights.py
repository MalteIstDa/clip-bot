"""Spannende Stellen finden: Lautstaerke-Spitzen + YouTube-"Most replayed"-Daten."""
from __future__ import annotations

import subprocess

import numpy as np

RATE = 8000  # Hz, reicht fuer die Lautstaerke-Analyse


def loudness_per_second(video_path) -> np.ndarray:
    """Lautstaerke (dB) pro Sekunde; liest den Ton als Stream, braucht kaum Speicher."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(video_path), "-vn", "-ac", "1",
           "-ar", str(RATE), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    step = RATE * 2  # Bytes pro Sekunde
    values, rest = [], b""
    while True:
        chunk = proc.stdout.read(step * 120)
        if not chunk:
            break
        data = rest + chunk
        usable = len(data) - len(data) % step
        rest = data[usable:]
        if usable:
            samples = np.frombuffer(data[:usable], dtype=np.int16).astype(np.float32)
            rms = np.sqrt((samples.reshape(-1, RATE) ** 2).mean(axis=1))
            values.append(20 * np.log10(np.maximum(rms, 1.0) / 32768.0))
    proc.wait()
    if proc.returncode != 0 or not values:
        raise RuntimeError(f"Ton von {video_path} konnte nicht gelesen werden")
    return np.concatenate(values)


def _zscore(x: np.ndarray) -> np.ndarray:
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    return (x - med) / (mad if mad > 1e-6 else (x.std() or 1.0))


def _smooth(x: np.ndarray, width: int) -> np.ndarray:
    width = max(1, min(width, len(x)))
    return np.convolve(x, np.ones(width) / width, mode="same")


def find_windows(loudness_db: np.ndarray, heatmap: list[dict], count: int, min_len: float,
                 max_len: float, skip_start: float = 0, skip_end: float = 0) -> list[tuple[float, float, float]]:
    """Gibt bis zu `count` nicht ueberlappende Fenster (start, ende, score) zurueck, beste zuerst."""
    n = len(loudness_db)
    target = int((min_len + max_len) / 2)
    if n < target + 10:
        return []

    # Laute Momente (Schreien, Lachen, Action) zaehlen, Stille nicht.
    score = np.clip(_zscore(_smooth(loudness_db, 3)), -1.0, 4.0)

    if heatmap:  # Zuschauer-Wiederholungen sind das staerkste Signal
        heat = np.zeros(n)
        for seg in heatmap:
            a, b = int(seg["start_time"]), int(np.ceil(seg["end_time"]))
            heat[max(a, 0):min(b, n)] = seg["value"]
        score = score + 2.0 * np.clip(_zscore(heat), -1.0, 4.0)

    window = _smooth(score, target)  # Mittelwert ueber die Clip-Laenge, zentriert
    half = target // 2
    valid = np.full(n, True)
    valid[: int(skip_start) + half] = False
    valid[n - int(skip_end) - half:] = False

    picks = []
    for _ in range(count):
        if not valid.any():
            break
        center = int(np.argmax(np.where(valid, window, -np.inf)))
        picks.append((float(center - half), float(center - half + target), float(window[center])))
        lo, hi = max(0, center - target - 15), min(n, center + target + 15)
        valid[lo:hi] = False
    return picks


def snap_to_speech(words, start: float, end: float, min_len: float, max_len: float) -> tuple[float, float]:
    """Verschiebt Start/Ende auf Sprechpausen, damit der Clip nicht mitten im Satz beginnt oder endet."""
    if len(words) < 4:
        return start, end
    starts, ends = [words[0].start], []
    for prev, cur in zip(words, words[1:]):
        if cur.start - prev.end > 0.45 or prev.text.rstrip().endswith((".", "!", "?")):
            ends.append(prev.end)
            starts.append(cur.start)
    ends.append(words[-1].end)

    new_start = min(starts, key=lambda t: abs(t - start))
    if abs(new_start - start) > 12:
        new_start = start
    fitting = [t for t in ends if min_len <= t - new_start <= max_len]
    if fitting:
        new_end = min(fitting, key=lambda t: abs(t - end))
    else:
        new_end = min(max(end, new_start + min_len), new_start + max_len)
    return max(0.0, new_start - 0.15), new_end + 0.25
