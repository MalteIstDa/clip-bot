"""Sprache -> Text mit Wort-Zeitstempeln (faster-whisper, laeuft lokal)."""
from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("clipbot")
_model = None
_device = None  # "cuda" oder "cpu"


@dataclass
class Word:
    start: float
    end: float
    text: str


def _load(model_name: str, cpu_only: bool = False):
    global _model, _device
    if cpu_only and _device != "cpu":
        _model = None
    if _model is None:
        from faster_whisper import WhisperModel
        if not cpu_only:
            try:
                _model, _device = WhisperModel(model_name, device="cuda", compute_type="float16"), "cuda"
            except Exception:  # keine (passende) Grafikkarte -> CPU
                pass
        if _model is None:
            _model, _device = WhisperModel(model_name, device="cpu", compute_type="int8"), "cpu"
    return _model


def _words(wav: Path, model_name: str, language: str | None, offset: float) -> list[Word]:
    segments, _ = _load(model_name).transcribe(str(wav), language=language, word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(w.start + offset, w.end + offset, text))
    return words


def transcribe_range(video_path, start: float, end: float, model_name: str,
                     language: str | None = None) -> list[Word]:
    """Transkribiert nur den Abschnitt [start, end]; Zeiten beziehen sich auf das ganze Video."""
    start = max(0.0, start)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "part.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
             "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
            check=True,
        )
        try:
            return _words(wav, model_name, language, start)
        except Exception as exc:
            # Die Grafikkarte wird erkannt, aber ihre Rechenbibliotheken (cuBLAS/cuDNN) fehlen oft.
            if _device != "cuda":
                raise
            log.info("Grafikkarte nicht nutzbar (%s). Die Spracherkennung laeuft auf dem Prozessor weiter.", exc)
            _load(model_name, cpu_only=True)
            return _words(wav, model_name, language, start)
