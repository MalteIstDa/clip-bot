"""Gezeichnete, animierte Bilder fuer Erklaervideos: ein Mensch mit Organen.

- koerper_weg:   ein Gegenstand (Emoji) wandert sichtbar durch den Koerper, z. B. Kaugummi vom Mund in den Magen
- koerper_organ: ein Organ leuchtet auf, die Kamera zoomt hinein, ein Schild nennt es

Alles ist SVG + GSAP (nur fromTo, keine Zufallswerte), damit hyperframes jedes Bild exakt gleich rendert.
Koordinaten: die Figur ist 600 x 700 px gross und liegt 1:1 in der Szene.
"""
from __future__ import annotations

import html
import math

VISUALS = ("keine", "koerper_weg", "koerper_organ")

# Stationen mit Wegpunkten (x, y) in Figur-Koordinaten
STATIONS = {
    "aussen": [(520, 150), (430, 150)],
    "mund": [(300, 152)],
    "speiseroehre": [(300, 205), (302, 265), (312, 330), (330, 385)],
    "magen": [(350, 430)],
    "duenndarm": [(338, 492), (268, 508), (332, 530), (268, 552), (330, 574), (290, 590)],
    "dickdarm": [(222, 600), (218, 540), (222, 478), (300, 468), (380, 478), (384, 540), (380, 606)],
    "ausgang": [(318, 640), (300, 690)],
    "luftroehre": [(290, 200), (290, 260), (288, 290)],
    "lunge": [(232, 330)],
    "herz": [(318, 352)],
    "gehirn": [(300, 78)],
    "leber": [(236, 420)],
    "nieren": [(372, 505)],
}
ROUTES = [
    ["aussen", "mund", "speiseroehre", "magen", "duenndarm", "dickdarm", "ausgang"],
    ["aussen", "mund", "luftroehre", "lunge"],
]
ORGANS = tuple(STATIONS)
LABELS = {
    "aussen": "Außen", "mund": "Mund", "speiseroehre": "Speiseröhre", "magen": "Magen", "duenndarm": "Dünndarm",
    "dickdarm": "Dickdarm", "ausgang": "Ausgang", "luftroehre": "Luftröhre", "lunge": "Lunge", "herz": "Herz",
    "gehirn": "Gehirn", "leber": "Leber", "nieren": "Nieren",
}

FIG_W, FIG_H = 600, 700
FIG_LEFT, FIG_TOP = 240, 400  # Lage der Figur in der 1080x1920-Szene
FIG_SCALE = 1.3  # Figur wird groesser gezeichnet als ihre Koordinaten

# ---------------------------------------------------------------- Zeichnung
BODY_SVG = """
<svg class="fig-svg" width="600" height="700" viewBox="0 0 600 700" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="{p}-skin" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffe2c6"/><stop offset="1" stop-color="#f6c79f"/>
    </linearGradient>
  </defs>
  <!-- Koerper -->
  <path d="M120 300 C120 255 170 236 228 232 L372 232 C430 236 480 255 480 300 L470 560 C468 640 420 690 300 692
           C180 690 132 640 130 560 Z" fill="url(#{p}-skin)" opacity="0.96"/>
  <path d="M120 300 C96 330 86 420 92 520 L118 520 L132 330 Z" fill="url(#{p}-skin)"/>
  <path d="M480 300 C504 330 514 420 508 520 L482 520 L468 330 Z" fill="url(#{p}-skin)"/>
  <rect x="268" y="178" width="64" height="62" rx="20" fill="url(#{p}-skin)"/>
  <circle cx="300" cy="105" r="88" fill="url(#{p}-skin)"/>
  <path d="M214 92 C214 30 260 12 300 12 C344 12 388 32 388 92 C370 64 338 52 300 54 C262 54 232 66 214 92 Z" fill="#5b3a29"/>
  <circle cx="268" cy="108" r="9" fill="#2b2b2b"/><circle cx="332" cy="108" r="9" fill="#2b2b2b"/>
  <ellipse id="{p}-mouth" cx="300" cy="152" rx="22" ry="7" fill="#b4413c"/>
  <!-- Organe -->
  <g id="{p}-o-gehirn" class="organ"><ellipse cx="300" cy="78" rx="58" ry="34" fill="#f9a8d4" opacity="0.6"/></g>
  <g id="{p}-o-lunge" class="organ">
    <path d="M276 262 C230 262 196 300 192 360 C190 400 208 418 236 414 C262 410 276 380 278 340 Z" fill="#fda4af" opacity="0.85"/>
    <path d="M324 262 C370 262 404 300 408 360 C410 400 392 418 364 414 C338 410 324 380 322 340 Z" fill="#fda4af" opacity="0.85"/>
  </g>
  <g id="{p}-o-luftroehre" class="organ"><path d="M290 175 L290 290" stroke="#93c5fd" stroke-width="14" stroke-linecap="round" fill="none"/></g>
  <g id="{p}-o-herz" class="organ"><path d="M318 372 C290 352 288 328 306 324 C314 322 318 328 318 332 C318 328 324 322 332 324
        C350 328 346 352 318 372 Z" fill="#ef4444"/></g>
  <g id="{p}-o-leber" class="organ"><path d="M188 400 C200 380 268 382 296 396 C290 430 250 448 214 446 C192 444 182 422 188 400 Z" fill="#b45309" opacity="0.9"/></g>
  <g id="{p}-o-speiseroehre" class="organ"><path d="M300 160 C300 220 302 280 312 330 C318 360 326 380 334 396" stroke="#fb923c"
        stroke-width="16" stroke-linecap="round" fill="none" opacity="0.9"/></g>
  <g id="{p}-o-magen" class="organ"><path d="M330 392 C360 380 400 392 404 428 C408 470 370 484 336 474 C312 466 316 446 330 440
        C346 434 340 418 330 412 Z" fill="#fb923c"/></g>
  <g id="{p}-o-nieren" class="organ"><ellipse cx="232" cy="505" rx="16" ry="26" fill="#9f1239" opacity="0.55"/>
        <ellipse cx="372" cy="505" rx="16" ry="26" fill="#9f1239" opacity="0.55"/></g>
  <g id="{p}-o-dickdarm" class="organ"><path d="M300 640 C318 626 384 622 386 600 L386 486 C386 470 372 464 356 464 L244 464
        C228 464 216 470 216 486 L216 600" stroke="#d97706" stroke-width="24" stroke-linecap="round" fill="none" opacity="0.9"/></g>
  <g id="{p}-o-duenndarm" class="organ"><path d="M340 490 C290 490 262 496 266 508 C272 522 336 516 336 530 C336 546 266 538 266 552
        C266 566 334 560 334 574 C334 588 300 592 288 592" stroke="#fdba74" stroke-width="14" stroke-linecap="round" fill="none"/></g>
</svg>
"""

FIG_CSS = """
  .figwrap { position: absolute; left: %(left)dpx; top: %(top)dpx; width: 600px; height: 700px;
             transform: scale(%(scale)s); transform-origin: 50%% 0; }
  .fig { position: absolute; left: 0; top: 0; width: 600px; height: 700px; }
  .fig-svg { position: absolute; left: 0; top: 0; overflow: visible;
             filter: drop-shadow(0 30px 40px rgba(0,0,0,0.45)); }
  .obj { position: absolute; left: 0; top: 0; width: 90px; height: 90px; margin: -45px 0 0 -45px;
         font-size: 74px; line-height: 90px; text-align: center; filter: drop-shadow(0 6px 10px rgba(0,0,0,0.5)); }
  .ring { position: absolute; left: 0; top: 0; width: 150px; height: 150px; margin: -75px 0 0 -75px; border-radius: 50%%;
          border: 8px solid %(accent)s; box-sizing: border-box; }
  .label { position: absolute; left: 0; top: 0; padding: 10px 22px; border-radius: 999px; white-space: nowrap;
           background: %(accent)s; color: #111; font-family: 'Montserrat', sans-serif; font-weight: 900; font-size: 40px; }
  .vhead { position: absolute; left: 70px; right: 70px; top: 250px; text-align: center; z-index: 2;
           text-shadow: 0 6px 0 rgba(0,0,0,0.45), 0 0 30px rgba(0,0,0,0.6); }
"""


def _f(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def route(start: str, end: str) -> list[tuple[int, int]]:
    """Wegpunkte von start bis end auf einer passenden Route. Leer, wenn es keinen Weg gibt."""
    for r in ROUTES:
        if start in r and end in r and r.index(start) < r.index(end):
            pts = []
            for st in r[r.index(start): r.index(end) + 1]:
                pts += STATIONS[st]
            return pts
    return []


def figure_css(accent: str) -> str:
    return FIG_CSS % {"left": FIG_LEFT, "top": FIG_TOP, "scale": FIG_SCALE, "accent": accent}


def figure(sid: str, scene: dict, duration: float, start: float) -> tuple[str, list[str]]:
    """HTML der Figur und die GSAP-Zeilen. start = ab wann (Sekunden in der Szene) animiert wird."""
    p = f"{sid}-f"
    visual = scene.get("visual", "keine")
    target = scene.get("visual_to") if scene.get("visual_to") in STATIONS else "magen"
    origin = scene.get("visual_from") if scene.get("visual_from") in STATIONS else "aussen"
    pts = route(origin, target) if visual == "koerper_weg" else []
    if visual == "koerper_weg" and len(pts) < 2:
        visual = "koerper_organ"  # kein sinnvoller Weg: dann wenigstens das Ziel zeigen
    parts = [BODY_SVG.replace("{p}", p)]
    anims = [f'tl.fromTo("#{p}", {{opacity: 0, y: 80, scale: 0.92}}, {{opacity: 1, y: 0, scale: 1, duration: 0.5, '
             f'ease: "power3.out"}}, {_f(max(0.0, start - 0.35))});']
    tx, ty = STATIONS[target][-1] if visual == "koerper_weg" else STATIONS[target][0]

    if visual == "koerper_weg":
        emoji = html.escape(scene.get("emoji") or "●")
        parts.append(f'<div id="{p}-obj" class="obj">{emoji}</div>')
        # Zeit gleichmaessig nach Weglaenge verteilen
        seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        travel = max(1.0, min(duration - start - 0.8, 1.2 + sum(seg) / 260))
        t = start + 0.2
        parts[-1] = parts[-1].replace('class="obj"', f'class="obj" style="transform: translate({pts[0][0]}px, {pts[0][1]}px)"')
        anims.append(f'tl.fromTo("#{p}-obj", {{opacity: 0}}, {{opacity: 1, duration: 0.2}}, {_f(t - 0.2)});')
        scale = 1.0
        for i, d in enumerate(seg):
            t0, t1 = round(t, 3), round(t + travel * d / sum(seg), 3)
            dt = t1 - t0
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            scale_to = 0.6 if y1 > 160 else 1.0  # im Koerper etwas kleiner
            anims.append(f'tl.fromTo("#{p}-obj", {{x: {x0}, y: {y0}, scale: {scale}}}, {{x: {x1}, y: {y1}, '
                         f'scale: {scale_to}, duration: {_f(dt)}, ease: "none"}}, {_f(t0)});')
            scale = scale_to
            if pts[i] in STATIONS["mund"]:  # Schlucken: Mund geht auf und zu
                anims.append(f'tl.fromTo("#{p}-mouth", {{scaleY: 1}}, {{scaleY: 3, duration: 0.15, yoyo: true, repeat: 1, '
                             f'transformOrigin: "50% 50%"}}, {_f(max(0.0, t - 0.3))});')
            t = t1
        reached = [st for st in STATIONS if STATIONS[st][-1] == (x1, y1)]
        organ = reached[0] if reached else target
    else:
        organ = target
        t = start + 0.3

    # Ziel-Organ: aufleuchten, Ring, Schild, Kamera zoomt hinein
    anims.append(f'tl.fromTo("#{p}-o-{organ}", {{opacity: 0.55}}, {{opacity: 1, duration: 0.3, yoyo: true, repeat: 3}}, '
                 f'{_f(t)});')
    anims.append(f'tl.fromTo("#{p}-o-{organ}", {{scale: 1}}, {{scale: 1.12, duration: 0.3, yoyo: true, repeat: 3, '
                 f'transformOrigin: "{tx}px {ty}px"}}, {_f(t)});')
    parts.append(f'<div id="{p}-ring" class="ring" style="transform: translate({tx}px, {ty}px)"></div>')
    anims.append(f'tl.fromTo("#{p}-ring", {{x: {tx}, y: {ty}, scale: 0.3, opacity: 0}}, '
                 f'{{x: {tx}, y: {ty}, scale: 1, opacity: 1, duration: 0.35, ease: "back.out(2)"}}, {_f(t)});')
    lx = tx + 90 if tx < 330 else tx - 90 - 30 * len(LABELS[organ])
    parts.append(f'<div id="{p}-lab" class="label">{html.escape(LABELS[organ])}</div>')
    anims.append(f'tl.fromTo("#{p}-lab", {{x: {lx}, y: {ty - 34}, opacity: 0, scale: 0.7}}, '
                 f'{{x: {lx}, y: {ty - 34}, opacity: 1, scale: 1, duration: 0.3, ease: "back.out(2)"}}, {_f(t + 0.15)});')
    zoom_start = t + 0.2
    zoom = max(0.6, duration - zoom_start - 0.1)
    anims.append(f'tl.fromTo("#{p}", {{scale: 1}}, {{scale: 1.12, duration: {_f(zoom)}, ease: "sine.inOut", '
                 f'transformOrigin: "{tx}px {ty}px"}}, {_f(zoom_start)});')
    return f'<div class="figwrap"><div id="{p}" class="fig">{"".join(parts)}</div></div>', baseline(anims)


def baseline(anims: list[str]) -> list[str]:
    """Mehrere fromTo auf dasselbe Element: nur das frueheste rendert sofort (Regel von hyperframes/GSAP)."""
    def pos(line: str) -> float:
        return float(line.rsplit(",", 1)[1].strip(" );"))
    first = {}
    for line in anims:
        sel = line.split('"')[1]
        if sel not in first or pos(line) < pos(first[sel]):
            first[sel] = line
    out = []
    for line in anims:
        sel = line.split('"')[1]
        if first[sel] is not line:
            head, tail = line.rsplit("}", 1)
            line = head + ", immediateRender: false}" + tail
        out.append(line)
    return out
