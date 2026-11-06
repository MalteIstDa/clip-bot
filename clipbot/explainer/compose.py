"""hyperframes-Projekt (HTML + GSAP) fuer ein Erklaervideo im Hochformat bauen und rendern.

Aufbau des Projekts:
  index.html               Wurzel: 1080x1920, Dauer = Tonspur, bindet Hintergrund, Szenen, Untertitel und Ton ein
  compositions/bg.html     bewegter Farbverlauf ueber die ganze Laenge
  compositions/sN.html     eine Szene je Gedanke (Hook, Fakt, Zahl, Vergleich, Schritte, Frage)
  compositions/captions.html  Untertitel Wort fuer Wort + Fortschrittsbalken
  assets/voice.mp3         Sprecherstimme
Regeln von hyperframes: jede getimte Ebene hat class="clip", data-start, data-duration; je Datei eine pausierte
GSAP-Timeline unter window.__timelines[id]; nur fromTo; keine Zufallswerte oder Uhrzeiten.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import shlex
import shutil
import subprocess
from pathlib import Path

from clipbot.explainer import illustrations

log = logging.getLogger("clipbot")

VENDOR = Path(__file__).resolve().parent / "vendor"
W, H = 1080, 1920
THEMES = [  # (Grund, Akzent, zweiter Akzent)
    ("#0f172a", "#facc15", "#38bdf8"),
    ("#1e1b4b", "#f472b6", "#a78bfa"),
    ("#052e2b", "#34d399", "#fbbf24"),
    ("#3b0a0a", "#fb923c", "#fde047"),
    ("#0c1a3a", "#60a5fa", "#f87171"),
]
FONT_HEAD = "'Archivo Black', 'Montserrat', sans-serif"
FONT_BODY = "'Montserrat', 'Inter', sans-serif"


def esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def theme_for(title: str) -> tuple[str, str, str]:
    return THEMES[int(hashlib.sha1(title.encode()).hexdigest(), 16) % len(THEMES)]


def _f(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


# ---------------------------------------------------------------- Szenen
SCENE_CSS = """
  #root { position: absolute; inset: 0; }
  .box { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; }
  .inner { position: absolute; left: 90px; right: 90px; top: 300px; height: 780px; display: flex;
           flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 34px; }
  .emoji { font-size: 190px; line-height: 1; }
  .head { font-family: %(head)s; font-size: 92px; line-height: 1.02; color: #fff; text-transform: uppercase;
          letter-spacing: -0.01em; }
  .head em { font-style: normal; color: %(accent)s; }
  .sub { font-family: %(body)s; font-weight: 700; font-size: 46px; line-height: 1.2; color: rgba(255,255,255,0.86); }
  .num { font-family: %(head)s; font-size: 170px; line-height: 1; color: %(accent)s; white-space: nowrap; }
  .cards { display: flex; flex-direction: column; gap: 26px; width: 100%%; }
  .card { border-radius: 30px; padding: 30px 34px; font-family: %(body)s; font-weight: 800; font-size: 50px;
          line-height: 1.15; color: #fff; text-align: left; background: rgba(255,255,255,0.10);
          border: 3px solid rgba(255,255,255,0.18); }
  .card b { color: %(accent)s; margin-right: 18px; }
  .card.bad { border-color: #f87171; } .card.good { border-color: #34d399; }
  .tagline { font-family: %(body)s; font-weight: 800; font-size: 34px; letter-spacing: 0.12em; text-transform: uppercase;
             color: %(accent2)s; }
"""


def _headline(text: str) -> str:
    """Letztes Wort der Headline in Akzentfarbe."""
    words = esc(text).split()
    if len(words) > 1:
        words[-1] = f"<em>{words[-1]}</em>"
    return " ".join(words)


def visual_scene_html(sid: str, scene: dict, duration: float, theme: tuple[str, str, str]) -> str:
    """Szene mit Zeichnung: Headline oben, darunter der animierte Koerper."""
    _, accent, accent2 = theme
    fig, anims = illustrations.figure(sid, scene, duration, start=0.5)
    head = (f'<div class="vhead"><div id="{sid}-head" class="head" style="font-size: 76px">'
            f'{_headline(scene["headline"])}</div></div>')
    anims.insert(0, f'tl.fromTo("#{sid}-head", {{opacity: 0, y: 50}}, {{opacity: 1, y: 0, duration: 0.4, '
                    f'ease: "power3.out"}}, 0.1);')
    if scene.get("number"):  # Zahl als Stempel neben der Figur
        head += (f'<div id="{sid}-num" class="num" style="position: absolute; right: 40px; top: 1150px; '
                 f'font-size: 130px; transform: rotate(-6deg)">{esc(scene["number"])}</div>')
        anims.append(f'tl.fromTo("#{sid}-num", {{opacity: 0, scale: 0.5}}, {{opacity: 1, scale: 1, duration: 0.4, '
                     f'ease: "back.out(2)"}}, 0.9);')
    css = (SCENE_CSS % {"head": FONT_HEAD, "body": FONT_BODY, "accent": accent, "accent2": accent2}
           + illustrations.figure_css(accent))
    return f"""<template>
<style>{css}</style>
<div id="root" data-composition-id="{sid}" data-width="{W}" data-height="{H}">
  <div id="{sid}-box" class="box clip" data-start="0" data-duration="{_f(duration)}">
    {head}
    {fig}
  </div>
</div>
<script>
  const tl = gsap.timeline({{ paused: true }});
  {chr(10).join("  " + a for a in anims)}
  window.__timelines["{sid}"] = tl;
</script>
</template>
"""


def scene_html(sid: str, scene: dict, duration: float, theme: tuple[str, str, str]) -> str:
    if scene.get("visual", "keine") in ("koerper_weg", "koerper_organ"):
        return visual_scene_html(sid, scene, duration, theme)
    _, accent, accent2 = theme
    kind = scene["kind"]
    parts, anims = [], []
    t = 0.15

    def add(cls: str, inner: str, extra_id: str, anim: str) -> None:
        nonlocal t
        parts.append(f'<div id="{sid}-{extra_id}" class="{cls}">{inner}</div>')
        anims.append(anim.format(sel=f"#{sid}-{extra_id}", t=_f(t)))
        t += 0.22

    pop = 'tl.fromTo("{sel}", {{opacity: 0, scale: 0.6, y: 40}}, {{opacity: 1, scale: 1, y: 0, duration: 0.45, ease: "back.out(1.8)"}}, {t});'
    rise = 'tl.fromTo("{sel}", {{opacity: 0, y: 60}}, {{opacity: 1, y: 0, duration: 0.4, ease: "power3.out"}}, {t});'

    if kind == "hook":
        add("tagline", "Wusstest du …?", "tag", rise)
    if scene.get("emoji"):
        add("emoji", esc(scene["emoji"]), "emoji", pop)
    if kind == "number" and scene.get("number"):
        add("num", esc(scene["number"]), "num", pop)
    add("head", _headline(scene["headline"]), "head", rise)
    items = [i for i in scene.get("items") or [] if i.strip()]
    if kind == "compare" and len(items) >= 2:
        cards = (f'<div id="{sid}-c0" class="card bad"><b>✗</b>{esc(items[0])}</div>'
                 f'<div id="{sid}-c1" class="card good"><b>✓</b>{esc(items[1])}</div>')
        parts.append(f'<div class="cards">{cards}</div>')
        for i in range(2):
            anims.append(rise.format(sel=f"#{sid}-c{i}", t=_f(t)))
            t += 0.5
    elif kind == "steps" and items:
        cards = "".join(f'<div id="{sid}-c{i}" class="card"><b>{i + 1}</b>{esc(x)}</div>' for i, x in enumerate(items[:4]))
        parts.append(f'<div class="cards">{cards}</div>')
        step = max(0.35, min(1.2, (duration - t - 0.5) / max(1, len(items[:4]))))
        for i in range(len(items[:4])):
            anims.append(rise.format(sel=f"#{sid}-c{i}", t=_f(t)))
            t += step
    elif scene.get("sub"):
        add("sub", esc(scene["sub"]), "sub", rise)
    # leichtes Atmen der Headline, damit das Bild nie stillsteht
    anims.append(f'tl.fromTo("#{sid}-head", {{scale: 1}}, {{scale: 1.04, duration: {_f(max(0.5, duration - 1))}, '
                 f'ease: "sine.inOut"}}, 0.9);')
    css = SCENE_CSS % {"head": FONT_HEAD, "body": FONT_BODY, "accent": accent, "accent2": accent2}
    return f"""<template>
<style>{css}</style>
<div id="root" data-composition-id="{sid}" data-width="{W}" data-height="{H}">
  <div id="{sid}-box" class="box clip" data-start="0" data-duration="{_f(duration)}">
    <div class="inner">
      {"".join(parts)}
    </div>
  </div>
</div>
<script>
  const tl = gsap.timeline({{ paused: true }});
  {chr(10).join("  " + a for a in anims)}
  window.__timelines["{sid}"] = tl;
</script>
</template>
"""


# ---------------------------------------------------------------- Hintergrund
def bg_html(duration: float, theme: tuple[str, str, str]) -> str:
    base, accent, accent2 = theme
    return f"""<template>
<style>
  #root {{ position: absolute; inset: 0; }}
  .bg {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; background: {base}; overflow: hidden; }}
  .blob {{ position: absolute; width: 900px; height: 900px; border-radius: 50%; filter: blur(140px); opacity: 0.55; }}
  #bg-a {{ left: -260px; top: 120px; background: {accent}; }}
  #bg-b {{ left: 420px; top: 980px; background: {accent2}; }}
  .shade {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px;
            background: linear-gradient(180deg, rgba(0,0,0,0.25), rgba(0,0,0,0) 30%, rgba(0,0,0,0) 60%, rgba(0,0,0,0.55)); }}
</style>
<div id="root" data-composition-id="bg" data-width="{W}" data-height="{H}">
  <div id="bg-box" class="bg clip" data-start="0" data-duration="{_f(duration)}" data-layout-ignore>
    <div id="bg-a" class="blob"></div>
    <div id="bg-b" class="blob"></div>
    <div class="shade"></div>
  </div>
</div>
<script>
  const tl = gsap.timeline({{ paused: true }});
  tl.fromTo("#bg-a", {{x: 0, y: 0}}, {{x: 380, y: 260, duration: {_f(duration)}, ease: "sine.inOut"}}, 0);
  tl.fromTo("#bg-b", {{x: 0, y: 0}}, {{x: -420, y: -380, duration: {_f(duration)}, ease: "sine.inOut"}}, 0);
  window.__timelines["bg"] = tl;
</script>
</template>
"""


# ---------------------------------------------------------------- Untertitel + Fortschritt
def caption_groups(words, per_group: int = 3) -> list[list]:
    groups, cur = [], []
    for w in words:
        pause = cur and w.start - cur[-1].end >= 0.15
        if cur and (len(cur) >= per_group or pause or cur[-1].text.endswith((".", "!", "?", ","))):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def captions_html(words, duration: float, theme: tuple[str, str, str]) -> str:
    _, accent, _ = theme
    groups = caption_groups(words)
    parts, anims = [], []
    for g, group in enumerate(groups):
        start = max(0.1, group[0].start - 0.05)
        end = groups[g + 1][0].start - 0.02 if g + 1 < len(groups) else min(duration, group[-1].end + 0.6)
        end = max(end, start + 0.2)
        spans = " ".join(f'<span id="cw-{g}-{i}" class="w">{esc(w.text)}</span>' for i, w in enumerate(group))
        parts.append(f'<div id="cg-{g}" class="grp clip" data-start="{_f(start)}" data-duration="{_f(end - start)}">'
                     f'<div id="cgi-{g}" class="line">{spans}</div></div>')
        anims.append(f'tl.fromTo("#cgi-{g}", {{scale: 0.8, opacity: 0}}, {{scale: 1, opacity: 1, duration: 0.14, '
                     f'ease: "back.out(2)"}}, {_f(start)});')
        for i, w in enumerate(group):
            anims.append(f'tl.fromTo("#cw-{g}-{i}", {{color: "#ffffff"}}, {{color: "{accent}", duration: 0.05}}, '
                         f'{_f(max(start, w.start))});')
    return f"""<template>
<style>
  #root {{ position: absolute; inset: 0; }}
  .grp {{ position: absolute; left: 0; top: 1370px; width: {W}px; height: 170px; overflow: visible; }}
  .line {{ width: 100%; text-align: center; font-family: {FONT_HEAD}; font-size: 76px; line-height: 1.05;
           text-transform: uppercase; color: #fff; padding: 0 70px; box-sizing: border-box;
           text-shadow: 0 6px 0 rgba(0,0,0,0.55), 0 0 24px rgba(0,0,0,0.5); }}
  .bar {{ position: absolute; left: 90px; top: 200px; width: 900px; height: 12px; border-radius: 6px;
          background: rgba(255,255,255,0.18); overflow: hidden; }}
  #cap-fill {{ width: 900px; height: 12px; border-radius: 6px; background: {accent}; transform-origin: 0 50%; }}
</style>
<div id="root" data-composition-id="captions" data-width="{W}" data-height="{H}">
  <div id="cap-bar" class="bar clip" data-start="0" data-duration="{_f(duration)}" data-layout-ignore><div id="cap-fill"></div></div>
  {chr(10).join("  " + p for p in parts)}
</div>
<script>
  const tl = gsap.timeline({{ paused: true }});
  tl.fromTo("#cap-fill", {{scaleX: 0}}, {{scaleX: 1, duration: {_f(duration)}, ease: "none"}}, 0);
  {chr(10).join("  " + a for a in anims)}
  window.__timelines["captions"] = tl;
</script>
</template>
"""


# ---------------------------------------------------------------- Projekt
def _host(cid: str, start: float, duration: float, track: int, kind: str = "graphics") -> str:
    return (f'<div id="{cid}" data-composition-id="{cid}" data-composition-src="compositions/{cid}.html" '
            f'data-start="{_f(start)}" data-duration="{_f(duration)}" data-track-index="{track}" '
            f'data-track-kind="{kind}" data-width="{W}" data-height="{H}" style="z-index: {track}"></div>')


def build_project(project: Path, script: dict, voice: dict) -> Path:
    """Schreibt das hyperframes-Projekt. voice = Ergebnis von voice.speak (Audio liegt schon in assets/)."""
    comps = project / "compositions"
    comps.mkdir(parents=True, exist_ok=True)
    (project / "assets").mkdir(exist_ok=True)
    shutil.copy(VENDOR / "gsap.min.js", project / "assets" / "gsap.min.js")  # offline, feste Version
    theme = theme_for(script["title"])
    total = round(voice["duration"] + 0.4, 2)
    hosts = [_host("bg", 0, total, 1)]
    for i, (scene, (start, end)) in enumerate(zip(script["scenes"], voice["scenes"]), 1):
        sid = f"s{i}"
        dur = max(0.5, (end if i < len(script["scenes"]) else total) - start)
        (comps / f"{sid}.html").write_text(scene_html(sid, scene, dur, theme), encoding="utf-8")
        hosts.append(_host(sid, start, dur, 2))
    (comps / "bg.html").write_text(bg_html(total, theme), encoding="utf-8")
    (comps / "captions.html").write_text(captions_html(voice["words"], total, theme), encoding="utf-8")
    hosts.append(_host("captions", 0, total, 3, "captions"))
    index = f"""<!doctype html>
<html lang="de">
<head><meta charset="utf-8"><title>{esc(script['title'])}</title>
<style>html, body {{ margin: 0; background: #000; }}</style>
<script src="assets/gsap.min.js"></script></head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="{_f(total)}" data-width="{W}" data-height="{H}" data-fps="30">
    {chr(10).join("    " + h for h in hosts)}
    <audio id="voice" class="clip" src="assets/voice.mp3" data-start="0" data-track-index="4" data-volume="1"></audio>
  </div>
  <script>
    window.__timelines["main"] = gsap.timeline({{ paused: true }});
  </script>
</body>
</html>
"""
    (project / "index.html").write_text(index, encoding="utf-8")
    (project / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    return project / "index.html"


def render(project: Path, out: Path, cmd: str, quality: str = "looks", timeout: int = 1800) -> Path:
    """Rendert mit der hyperframes-CLI (Node.js 22+ noetig)."""
    base = shlex.split(cmd)
    if not shutil.which(base[0]):
        raise RuntimeError(f"'{base[0]}' nicht gefunden. Fuer Erklaervideos wird Node.js 22 oder neuer gebraucht (nodejs.org).")
    lint = subprocess.run(base + ["lint"], cwd=project, capture_output=True, text=True, timeout=600)
    if lint.returncode != 0:
        log.info("hyperframes lint meldet: %s", (lint.stdout + lint.stderr).strip()[-800:])
    result = subprocess.run(base + ["render", "--quality", quality, "-o", str(out.resolve()), "--quiet"],
                            cwd=project, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError("hyperframes render fehlgeschlagen: " + (result.stdout + result.stderr).strip()[-1200:])
    return out
