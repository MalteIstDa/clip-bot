"""Facecam im Stream finden und genau ausschneiden, damit Gaming-Clips das Gesicht oben gross zeigen.

1. Gesichter: Gesichtsfinder von OpenCV (Haar-Kaskade) in einigen Bildern des Abschnitts. Eine Facecam ist ein
   kleines Gesicht, das in den meisten Bildern an derselben Stelle sitzt.
2. Rahmen: Die Facecam ist ein festes Fenster im Stream. Seine Kanten bleiben stehen, waehrend sich Spiel und
   Kamerabild bewegen. ClipBot sucht deshalb um das Gesicht herum nach Kanten, die in fast allen Bildern an
   derselben Stelle liegen, und schneidet genau das Fenster aus. Ohne erkennbaren Rahmen nimmt er Kopf und
   Schultern grosszuegig um das Gesicht.
Ist das Gesicht gross (Vollbild-Kamera, z. B. Just Chatting) oder springt es herum, gibt es keine Facecam.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("clipbot")

SAMPLES = 12              # so viele Bilder werden untersucht
MAX_FACE = 0.22           # groesser (Anteil der Bildbreite): keine Facecam, sondern Vollbild-Kamera
MIN_HITS = 0.4            # in so vielen Bildern muss das Gesicht an derselben Stelle sein
STATIC = 0.7              # so viele Bilder muss eine Kante gemeinsam haben, damit sie als Rahmen zaehlt


def _read(source: Path, start: float, end: float):
    """Gesichter je Bild, Karte der stehenden Kanten (oder None), Breite, Hoehe. None ohne OpenCV."""
    try:
        import cv2
        import numpy as np
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except (ImportError, AttributeError):
        log.info("Facecam-Bild braucht das Paket opencv-python-headless in Version 4 "
                 "(pip install -r requirements.txt).")
        return None
    cap = cv2.VideoCapture(str(source))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    found, edges, motion, last = [], None, None, None
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
            e = cv2.dilate(cv2.Canny(gray, 40, 120), np.ones((3, 3), np.uint8)) > 0
            edges = e.astype(np.uint16) if edges is None else edges + e
            small = cv2.resize(gray, (width // 4, height // 4)).astype(np.float32)
            if last is not None:
                diff = np.abs(small - last)
                motion = diff if motion is None else motion + diff
            last = small
    finally:
        cap.release()
    static = (edges >= STATIC * len(found)) if edges is not None and found else None
    if motion is not None:
        motion = cv2.resize(motion / max(1, len(found) - 1), (width, height))
    return found, (static, motion), width, height


def face_of(frames, width: int):
    """Die Facecam-Stelle: (Mitte x, Mitte y, Gesichtsbreite) oder None."""
    small = [(x, y, w, h) for faces in frames for (x, y, w, h) in faces if w < MAX_FACE * width]
    if not small:
        return None

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
    mid = len(best) // 2
    return (sorted(center(f)[0] for f in best)[mid], sorted(center(f)[1] for f in best)[mid],
            sorted(f[2] for f in best)[mid])


def _border(profile, start: int, stop: int, step: int) -> int | None:
    """Erste Position von start Richtung stop, an der eine Kante (fast) durchgehend steht."""
    for pos in range(start, stop, step):
        if 0 <= pos < len(profile) and profile[pos] >= 0.9:
            return pos
    return None


def frame_box(maps, cx: float, cy: float, fw: float, width: int, height: int):
    """Rahmen der Facecam aus der Karte der stehenden Kanten. (x, y, w, h) oder None.
    Eine Rahmenkante ist eine gerade Linie, die in fast allen Bildern ueber die ganze Hoehe (bzw. Breite) um das
    Gesicht steht. Fehlt eine Seite (weicher Uebergang), ergibt sie sich aus dem 16:9-Format einer Webcam."""
    if maps is None or maps[0] is None:
        return None
    static, motion = maps
    reach = int(fw * 5)  # so weit darf der Rahmen hoechstens vom Gesicht weg sein
    ys = slice(int(max(0, cy - fw)), int(min(height, cy + fw)))
    xs = slice(int(max(0, cx - fw)), int(min(width, cx + fw)))
    col = static[ys, :].mean(axis=0)  # Anteil Kante je Spalte (linke/rechte Kante)
    row = static[:, xs].mean(axis=1)  # Anteil Kante je Zeile (obere/untere Kante)

    def moving(x0, x1, y0, y1) -> float:
        if motion is None:
            return 0.0
        x0, x1, y0, y1 = (int(max(0, min(v, lim))) for v, lim in ((x0, width), (x1, width), (y0, height), (y1, height)))
        return float(motion[y0:y1, x0:x1].mean()) if x1 > x0 and y1 > y0 else 0.0

    def is_border(pos: int, vertical: bool, outward: int) -> bool:
        """Eine Rahmenkante trennt das ruhige Kamerabild vom bewegten Spiel. Linien im Zimmer hinter dem
        Streamer (Tuer, Regal) stehen auch, haben aber auf beiden Seiten wenig Bewegung."""
        band = max(6, int(fw * 0.5))
        if vertical:
            inside = moving(pos - outward * band, pos - outward * 4, ys.start, ys.stop) if outward > 0 else \
                moving(pos + 4, pos + band, ys.start, ys.stop)
            outside = moving(pos + 4, pos + band, ys.start, ys.stop) if outward > 0 else \
                moving(pos - band, pos - 4, ys.start, ys.stop)
        else:
            inside = moving(xs.start, xs.stop, pos - outward * band, pos - outward * 4) if outward > 0 else \
                moving(xs.start, xs.stop, pos + 4, pos + band)
            outside = moving(xs.start, xs.stop, pos + 4, pos + band) if outward > 0 else \
                moving(xs.start, xs.stop, pos - band, pos - 4)
        return motion is None or outside > max(2.0, inside * 1.6)

    def search(profile, start, stop, step, vertical):
        """Bis zu 3 Kandidaten fuer eine Rahmenkante, vom Gesicht nach aussen."""
        hits, skip = [], None
        for pos in range(start, stop, step):
            if skip is not None and abs(pos - skip) < 10 or not 0 <= pos < len(profile) or profile[pos] < 0.6:
                continue
            verified = is_border(pos, vertical, step)  # draussen bewegt sich das Spiel
            if verified or profile[pos] >= 0.97:       # oder eine durchgehende Linie (z. B. Overlay darunter)
                hits.append((pos, verified))
                skip = pos
                if len(hits) == 3:
                    break
        if any(v for _, v in hits):  # bestaetigte Rahmenkanten schlagen Linien im Zimmer
            hits = [h for h in hits if h[1]]
        return [pos for pos, _ in hits]

    left = search(col, int(cx - fw * 0.9), int(cx - reach) - 1, -1, True)
    right = search(col, int(cx + fw * 0.9), int(cx + reach) + 1, 1, True)
    top = search(row, int(cy - fw * 0.9), int(cy - reach) - 1, -1, False)
    bottom = search(row, int(cy + fw * 0.9), int(cy + reach) + 1, 1, False)
    # Fehlende Seiten: entweder der Bildrand (wenn in Reichweite) oder aus dem 16:9-Format einer Webcam.
    # Alle Moeglichkeiten durchprobieren und die glaubwuerdigste nehmen.
    import itertools
    import math

    def options(found, edge, in_reach):
        # Kandidaten-Linien, dazu der Bildrand (wenn in Reichweite) und "aus dem Format ableiten"
        return [(pos, 0.03 * i) for i, pos in enumerate(found)] + ([(edge, 0.02)] if in_reach else []) + \
            [(None, 0.05 if not found else 0.3)]

    best, best_score = None, 1e9
    for (l, pl), (r, pr), (tp, pt), (bt, pb) in itertools.product(
            options(left, 0, cx - reach <= 0), options(right, width, cx + reach >= width),
            options(top, 0, cy - reach <= 0), options(bottom, height, cy + reach >= height)):
        w = r - l if l is not None and r is not None else None
        h = bt - tp if tp is not None and bt is not None else None
        if w is None and h is None:
            continue
        if w is None:
            w = h * 16 / 9
            l = l if l is not None else (r - w if r is not None else cx - w / 2)
            r = l + w
        if h is None:
            h = w * 9 / 16
            tp = tp if tp is not None else (bt - h if bt is not None else cy - h * 0.45)
            bt = tp + h
        l, tp, r, bt = max(0, l), max(0, tp), min(width, r), min(height, bt)
        w, h = r - l, bt - tp
        if w < fw * 2.2 or h < fw * 1.5 or w > min(width * 0.45, fw * 7) or not (l < cx < r and tp < cy < bt):
            continue
        # Moeglichst Webcam-Format, und das Gesicht nimmt in einer Facecam meist etwa ein Fuenftel der Breite ein
        score = abs(math.log((w / h) / (16 / 9))) + 0.3 * abs(math.log((fw / w) / 0.19)) + pl + pr + pt + pb
        if score < best_score:
            best, best_score = (l, tp, w, h), score
    if not best or best_score > 0.9:  # bis etwa 4:5 (hochkant geschnittene Kamera)
        return None  # kein glaubwuerdiges Kamerafenster
    left, top, w, h = best
    pad = 3  # Rahmenlinie selbst nicht mitnehmen
    x, y = int(left) + pad, int(top) + pad
    return x // 2 * 2, y // 2 * 2, int(w - 2 * pad) // 2 * 2, int(h - 2 * pad) // 2 * 2


def around_face(cx: float, cy: float, fw: float, width: int, height: int):
    """Ersatz ohne Rahmen: Kopf und Schultern grosszuegig, im 16:9-Format einer Webcam."""
    w = min(width, int(fw * 4.0))
    h = min(height, int(w * 9 / 16))
    w = min(w, int(h * 16 / 9))
    x = int(min(max(0, cx - w / 2), width - w))
    y = int(min(max(0, cy - h * 0.42), height - h))
    # Facecams kleben meist an einer Ecke: nahe Raender ganz mitnehmen
    if x < 0.12 * width:
        x = 0
    elif width - (x + w) < 0.12 * width:
        x = width - w
    if y < 0.15 * height:
        y = 0
    elif height - (y + h) < 0.15 * height:
        y = height - h
    return x // 2 * 2, y // 2 * 2, w // 2 * 2, h // 2 * 2


def box_from(frames, static, width: int, height: int):
    if not frames or not width or not height:
        return None
    face = face_of(frames, width)
    if not face:
        return None
    box = frame_box(static, *face, width, height)
    if box:
        return box
    # Ohne Rahmen nur, wenn das Gesicht am Rand sitzt (dort liegen Facecams). Ein Gesicht in der Bildmitte
    # gehoert meist zu einer Spielfigur oder einer Zwischensequenz.
    if 0.38 * width < face[0] < 0.62 * width:
        return None
    return around_face(*face, width, height)


def box_from_faces(frames, width: int, height: int):
    """Nur aus den Gesichtern (ohne Rahmen-Suche)."""
    return box_from(frames, None, width, height)


def find(source: Path, start: float, end: float):
    """Facecam im Abschnitt suchen. (x, y, w, h) in Pixeln des Videos oder None."""
    try:
        result = _read(Path(source), start, end)
        return box_from(*result) if result else None
    except Exception as exc:
        log.info("Facecam-Suche fehlgeschlagen: %s", exc)
        return None
