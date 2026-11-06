"""Ein Erklaervideo von der Idee bis zur Freigabe-Warteschlange."""
from __future__ import annotations

import hashlib
import logging
import re
import time

from clipbot import ai
from clipbot.explainer import compose, script as scripting, voice
from clipbot.state import State

log = logging.getLogger("clipbot")


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return s.strip("-")[:40] or "video"


def platform_texts(sc: dict, channel: dict) -> dict:
    tags = " ".join(sc["hashtags"])
    credit = str(channel.get("credit") or "").strip()
    title = sc["youtube_title"].strip()[:90]
    if "#shorts" not in title.lower():
        title += " #shorts"
    caption = sc["caption"].strip()
    tail = f"\n\n{credit}" if credit else ""
    return {
        "youtube": {"title": title[:100], "caption": f"{caption}{tail}\n\n{tags} #shorts"},
        "tiktok": {"title": title[:100], "caption": f"{caption} {tags}{tail}"},
        "instagram": {"title": title[:100], "caption": f"{caption}{tail}\n\n{tags}"},
    }


def create(settings: dict, channel: dict, state: State, topic: str, hook: str = "") -> str:
    """Skript -> Faktencheck -> Stimme -> hyperframes -> Clip (wartet auf Freigabe). Gibt die clip_id zurueck."""
    if not ai.enabled(settings):
        raise ai.AIError("Fuer Erklaervideos muss unter Einstellungen eine KI (Claude oder Ollama) gewaehlt sein.")
    name = channel["name"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    video_id = f"exp-{stamp}-{hashlib.sha1(topic.encode()).hexdigest()[:6]}"
    project = settings["work_dir"] / name / "projects" / f"{video_id}-{slug(topic)}"

    log.info("[%s] Erklaervideo: Skript zu '%s' ...", name, topic)
    sc = scripting.write(settings, topic, hook)
    log.info("[%s] Faktencheck ...", name)
    check = scripting.fact_check(settings, sc)
    if check["verdict"] == "falsch":
        log.info("[%s] Faktencheck fand Fehler, Skript wird korrigiert ...", name)
        sc = scripting.revise(settings, sc, check)
        check = scripting.fact_check(settings, sc)
    log.info("[%s] %s (%d Woerter)", name, check["summary"], scripting.word_count(sc))

    log.info("[%s] Stimme (%s) ...", name, settings["voice_backend"])
    spoken = voice.speak(settings, sc["scenes"], project / "assets")
    log.info("[%s] Animation bauen und mit hyperframes rendern (%.0f s Video, das dauert einige Minuten) ...",
             name, spoken["duration"])
    compose.build_project(project, sc, spoken)
    out = settings["work_dir"] / name / "clips" / video_id / f"{video_id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    compose.render(project, out, settings["hyperframes_cmd"])

    reason = check["summary"]
    if spoken["duration"] < 61:
        warn = f"Achtung: Das Video ist nur {spoken['duration']:.0f} Sekunden lang (unter 1 Minute, keine Verguetung)."
        log.warning("[%s] %s", name, warn)
        reason = f"{warn} {reason}"
    texts = platform_texts(sc, channel)
    transcript = " ".join(s["voice"] for s in sc["scenes"])
    state.mark_video(video_id, name, sc["title"], "done")
    state.add_clip(video_id, video_id, name, out, texts["youtube"]["title"], texts["tiktok"]["caption"],
                   0, spoken["duration"], texts, sc["scenes"][0]["headline"], reason,
                   "wartet" if settings["require_approval"] else "freigegeben", transcript)
    log.info("[%s] Erklaervideo fertig: %s (wartet unter 'Freigabe')", name, sc["title"])
    return video_id
