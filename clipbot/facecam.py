"""Facecam im Stream finden, damit Gaming-Clips das Gesicht oben gross und das Spiel darunter zeigen.

Gesucht wird mit dem Gesichtsfinder von OpenCV (Haar-Kaskade) in einigen Bildern des Abschnitts. Eine Facecam
ist ein kleines Gesicht, das in den meisten Bildern an derselben Stelle sitzt. Ist das Gesicht gross (die Kamera
fuellt das Bild, z. B. Just Chatting) oder springt es herum, gibt es keine Facecam und der Clip bleibt normal.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("clipbot")

TOP_ASPECT = 1080 / 640   # Seitenverhaeltnis des oberen Bereichs im Clip
SAMPLES = 10              # so viele Bilder werden untersucht
MAX_FACE = 0.22           # groesser (Anteil der Bildbreite): keine Facecam, sondern Vollbild-Kamera
MIN_HITS = 0.4            # in so vielen Bildern muss das Gesicht an derselben Stelle sein


def _faces(source: Path, start: float, end: float) -> tuple[list[list[tuple[int, int, int, int]]], int, int] | None:
    """Gesichter je untersuchtem Bild, dazu Breite und Hoehe des Videos. None ohne OpenCV."""
    try:
        import cv2
    except ImportError:
        log.info("Facecam-Bild braucht das Paket opencv-python-headless (pip install -r requirements.txt).")
        return None
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(source))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    found = []
    try:
        for i in range(SAMPLES):
            t = start + (end - start) * (i + 0.5) / SAMPLES
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                             minSize=(max(24, width // 40), max(24, width // 40)))
            found.append([tuple(int(v) for v in f) for f in faces])
    finally:
        cap.release()
    return found, width, height


def box_from_faces(frames: list[list[tuple[int, int, int, int]]], width: int, height: int):
    """Aus den Gesichtern je Bild die Facecam bestimmen: (x, y, w, h) des Ausschnitts oder None."""
    if not frames or not width or not height:
        return None
    small = [(x, y, w, h) for faces in frames for (x, y, w, h) in faces if w < MAX_FACE * width]
    if not small:
        return None
    # Haeufigste Stelle: Gesichter, deren Mitte nahe beieinander liegt
    def center(f):
        return f[0] + f[2] / 2, f[1] + f[3] / 2
    best = []
    for f in small:
        cx, cy = center(f)
        near = [g for g in small if abs(center(g)[0] - cx) < f[2] * 0.6 and abs(center(g)[1] - cy) < f[3] * 0.6]
        if len(near) > len(best):
            best = near
    frames_with = sum(1 for faces in frames if any(f in best for f in faces))
    if frames_with < max(2, MIN_HITS * len(frames)):
        return None
    fw = sorted(f[2] for f in best)[len(best) // 2]
    cx = sorted(center(f)[0] for f in best)[len(best) // 2]
    cy = sorted(center(f)[1] for f in best)[len(best) // 2]
    # Ausschnitt um das Gesicht: etwa so breit wie eine typische Facecam (Kopf und Schultern)
    w = min(width, int(fw * 3.4))
    h = min(height, int(w / TOP_ASPECT))
    w = min(w, int(h * TOP_ASPECT))
    x = int(min(max(0, cx - w / 2), width - w))
    y = int(min(max(0, cy - h * 0.45), height - h))
    return x // 2 * 2, y // 2 * 2, w // 2 * 2, h // 2 * 2


def find(source: Path, start: float, end: float):
    """Facecam im Abschnitt suchen. (x, y, w, h) in Pixeln des Videos oder None."""
    try:
        result = _faces(Path(source), start, end)
    except Exception as exc:
        log.debug("Facecam-Suche: %s", exc)
        return None
    if not result:
        return None
    return box_from_faces(*result)
