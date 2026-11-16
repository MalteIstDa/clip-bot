"""Best-of-Videos: kurze Momente aus vielen Videos sammeln und zu einem 1-2-Minuten-Video zusammensetzen.

1. Beim Clippen eines Videos merkt sich `collect_moments` die staerksten kurzen Stellen (8 bis 22 Sekunden)
   als eigene kleine Dateien. So bleiben sie verfuegbar, auch wenn das Quellvideo geloescht wird.
2. `build` waehlt taeglich oder woechentlich die besten Momente der Creator eines Kanals, rendert jeden im
   Hochformat mit Untertiteln und dem Creator-Tag oben im Bild und haengt sie zusammen.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from clipbot import ai, config, highlights, lang, render, subtitles, transcribe, viral
from clipbot.state import State
from clipbot.transcribe import Word

log = logging.getLogger("clipbot")

MAX_AGE = {"daily": 2 * 86400, "weekly": 8 * 86400}  # so alt duerfen Momente fuer ein Best-of hoechstens sein
KEEP_DAYS = 14                                        # danach werden ungenutzte Moment-Dateien geloescht
AI_CANDIDATES = 24                                    # so viele Momente bekommt die KI hoechstens zur Auswahl


# ---------------------------------------------------------------- Momente sammeln
def collect_moments(source, info: dict, s: dict, state: State, creator_id: str, creator_name: str,
                    loud=None) -> int:
    """Speichert die staerksten kurzen Stellen eines Videos fuer spaetere Best-ofs. Gibt ihre Zahl zurueck."""
    if loud is None:
        loud = highlights.loudness_per_second(source)
    windows = highlights.find_windows(
        loud, info["heatmap"], s["moments_per_video"], s["moment_min_seconds"], s["moment_max_seconds"],
        s["skip_start_seconds"], s["skip_end_seconds"])
    folder = s["work_dir"] / "_momente" / str(info["id"])
    made = 0
    for n, (start, end, score) in enumerate(windows, 1):
        words = transcribe.transcribe_range(source, start - 6, end + 6, s["whisper_model"], s["language"])
        start, end = highlights.snap_to_speech(words, start, end, s["moment_min_seconds"], s["moment_max_seconds"])
        end = min(end, info["duration"])
        if end - start < 3:
            continue
        moment_id = f"{info['id']}_m{n}"
        path = render.cut_raw(source, start, end, folder / f"{moment_id}.mp4")
        relative = [[round(w.start - start, 3), round(w.end - start, 3), w.text]
                    for w in words if w.end > start and w.start < end]
        state.add_moment(moment_id, info["id"], creator_id, creator_name, info["title"], path, end - start,
                         float(score), viral.spoken_text(words, start, end), relative)
        made += 1
    return made


def cleanup(state: State, now: float | None = None) -> int:
    """Alte Moment-Dateien loeschen."""
    removed = 0
    for row in state.old_moments((now or time.time()) - KEEP_DAYS * 86400):
        path = Path(row["path"])
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()  # nur, wenn der Ordner leer ist
        except OSError:
            pass
        state.delete_moment(row["moment_id"])
        removed += 1
    return removed


# ---------------------------------------------------------------- Auswahl
def select(moments: list[dict], min_seconds: float, max_seconds: float, per_creator: int) -> list[dict]:
    """Beste Momente zuerst, hoechstens `per_creator` je Creator, zusammen hoechstens `max_seconds`.
    Reicht das Material sonst nicht fuer `min_seconds`, darf ein Creator oefter vorkommen."""
    ordered = sorted(moments, key=lambda m: m["score"], reverse=True)
    chosen, total, used = [], 0.0, {}
    for limit in (per_creator, None):
        for m in ordered:
            if m in chosen or total + m["duration"] > max_seconds:
                continue
            if limit is not None and used.get(m["creator"], 0) >= limit:
                continue
            chosen.append(m)
            total += m["duration"]
            used[m["creator"]] = used.get(m["creator"], 0) + 1
        if total >= min_seconds:
            break
    return arrange(chosen)


def arrange(chosen: list[dict]) -> list[dict]:
    """Der staerkste Moment zuerst (Hook), der zweitstaerkste am Schluss."""
    ranked = sorted(chosen, key=lambda m: m["score"], reverse=True)
    if len(ranked) < 3:
        return ranked
    return [ranked[0]] + ranked[2:] + [ranked[1]]


PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "headline": {"type": "string"}},
                "required": ["index", "headline"],
            },
        },
        "youtube_title": {"type": "string"},
        "youtube_description": {"type": "string"},
        "tiktok_caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["picks", "youtube_title", "youtube_description", "tiktok_caption", "hashtags"],
}


def _ai_plan(s: dict, channel: dict, moments: list[dict], label: str) -> dict | None:
    candidates = sorted(moments, key=lambda m: m["score"], reverse=True)[:AI_CANDIDATES]
    language = lang.of_channel(channel)["prompt"]
    listing = "\n\n".join(
        f"[{i}] Creator: {m['creator_name']} ({m['duration']:.0f} s, aus: {m['video_title']})\n"
        f"{(m['text'] or '')[:600] or '(kaum Sprache)'}" for i, m in enumerate(candidates))
    prompt = (
        f"Kanal: {channel['name']}. Aus diesen {len(candidates)} kurzen Momenten verschiedener Streamer und "
        f"YouTuber entsteht ein Best-of-Video im Hochformat ({label}).\n"
        f"Waehle die staerksten Momente: zusammen {s['bestof_min_seconds']} bis {s['bestof_max_seconds']} Sekunden, "
        f"hoechstens {s['bestof_max_per_creator']} je Creator, moeglichst viele verschiedene Creator. "
        "Reihenfolge in picks = Reihenfolge im Video: der staerkste Moment zuerst (er ist der Hook), "
        "der zweitstaerkste am Schluss.\n"
        f"- picks: index und headline je Moment. headline: 3 bis 6 Woerter auf {language}, die oben im Bild stehen, "
        "den Creator beim Namen nennen und sagen, was passiert (z. B. 'Gtastys unglaublicher Stunt').\n"
        "- youtube_title: hoechstens 70 Zeichen, macht neugierig, nennt 1 bis 2 der Creator, kein Hashtag.\n"
        "- youtube_description: 1 bis 2 Saetze.\n"
        "- tiktok_caption: 1 kurzer Satz plus Frage, welcher Moment der beste war, ohne Hashtags.\n"
        "- hashtags: 4 bis 6, breite Tags gemischt mit den Namen der Creator, keine Leerzeichen.\n"
        f"Schreibe alle Texte auf {language}. Versprich nichts, was die Momente nicht halten.\n\n" + listing
    )
    try:
        data = ai.generate_json(s, prompt, PICK_SCHEMA, viral.SYSTEM)
    except ai.AIError as exc:
        log.info("KI-Auswahl fuer das Best-of nicht moeglich (%s), nehme die lautesten Momente.", exc)
        return None
    picks, seen, total = [], set(), 0.0
    for entry in data["picks"]:
        i = entry["index"]
        if not 0 <= i < len(candidates) or i in seen:
            continue
        moment = candidates[i]
        if total + moment["duration"] > s["bestof_max_seconds"]:
            continue
        seen.add(i)
        total += moment["duration"]
        picks.append((moment, entry["headline"].strip()[:60] or moment["creator_name"]))
    if not picks:
        return None
    if total < s["bestof_min_seconds"]:  # die KI war zu sparsam: mit den naechstbesten auffuellen (vor dem Schluss)
        for moment in candidates:
            if total >= s["bestof_min_seconds"]:
                break
            if any(moment is m for m, _ in picks) or total + moment["duration"] > s["bestof_max_seconds"]:
                continue
            picks.insert(max(1, len(picks) - 1), (moment, moment["creator_name"]))
            total += moment["duration"]
    return {"picks": picks, "data": data}


def plan(s: dict, channel: dict, moments: list[dict], period: str) -> dict:
    """Welche Momente in welcher Reihenfolge, mit welcher Zeile oben, und die Texte zum Video.
    Gibt {"picks": [(moment, headline)], "title", "caption", "platforms", "label"} zurueck."""
    words = lang.of_channel(channel)
    label = words["bestof_weekly" if period == "weekly" else "bestof_daily"]
    result = _ai_plan(s, channel, moments, label) if ai.enabled(s) else None
    if result:
        picks, data = result["picks"], result["data"]
    else:
        chosen = select(moments, s["bestof_min_seconds"], s["bestof_max_seconds"], s["bestof_max_per_creator"])
        picks, data = [(m, m["creator_name"]) for m in chosen], {}

    names = list(dict.fromkeys(m["creator_name"] for m, _ in picks))
    credits = words["credits"].format(names=", ".join(names))
    fallback_title = f"{label}: {', '.join(names[:2])}" + (f" {words['and_more']}" if len(names) > 2 else "")
    title = (data.get("youtube_title") or fallback_title).strip()[:90]
    if "#shorts" not in title.lower():
        title += " #shorts"
    own_tags = list(channel.get("tags") or [])[:2]
    tags = viral.normalize_hashtags((data.get("hashtags") or words["bestof_tags"]) + own_tags, 6)
    yt_tags = viral.normalize_hashtags([t for t in tags if t.lower() != "#shorts"][:4] + ["shorts"], 5)
    description = (data.get("youtube_description") or words["bestof_caption"]).strip()
    tiktok = (data.get("tiktok_caption") or f"{label} 🔥 {words['bestof_caption']}").strip()
    platforms = {
        "youtube": {"title": title[:100], "caption": f"{description}\n\n{credits}\n\n{' '.join(yt_tags)}"},
        "tiktok": {"title": title[:100], "caption": f"{tiktok} {' '.join(tags)}\n\n{credits}"},
        "instagram": {"title": title[:100], "caption": f"{tiktok}\n\n{credits}\n\n{' '.join(tags)}"},
    }
    return {"picks": picks, "title": title[:100], "caption": platforms["tiktok"]["caption"],
            "platforms": platforms, "label": label, "names": names}


# ---------------------------------------------------------------- Video bauen
def available(cfg: dict, channel: dict, state: State, period: str, now: float | None = None) -> list[dict]:
    """Unverbrauchte Momente der Creator dieses Kanals, deren Datei noch da ist (mit aktuellen Anzeigenamen)."""
    creators = config.creators_of(cfg, channel)
    names = {c["id"]: c.get("name") or c["id"] for c in creators}
    since = (now or time.time()) - MAX_AGE.get(period, MAX_AGE["daily"])
    moments = [m for m in state.unused_moments(channel["name"], list(names), since) if Path(m["path"]).exists()]
    for m in moments:
        m["creator_name"] = names.get(m["creator"]) or m["creator_name"] or m["creator"]
    return moments


def build(cfg: dict, channel: dict, state: State, period: str | None = None, force: bool = False) -> str | None:
    """Baut ein Best-of fuer den Kanal und stellt es in die Freigabe. Gibt die clip_id zurueck,
    oder None, wenn das Material (noch) nicht reicht. force: auch unter der Mindestlaenge bauen."""
    s, name = cfg["settings"], channel["name"]
    period = period if period in MAX_AGE else (channel.get("bestof") if channel.get("bestof") in MAX_AGE else "daily")
    cleanup(state)
    moments = available(cfg, channel, state, period)
    if len(moments) < 2:
        log.info("[%s] Best-of: Es gibt noch zu wenige Momente (%d). Sie entstehen, sobald Videos der Creator "
                 "dieses Kanals geclippt werden.", name, len(moments))
        return None
    todo = plan(s, channel, moments, period)
    picks = todo["picks"]
    total = sum(m["duration"] for m, _ in picks)
    if total < s["bestof_min_seconds"] and not force:
        log.info("[%s] Best-of: Das Material reicht erst fuer %.0f von %d Sekunden. Es wird spaeter gebaut.",
                 name, total, s["bestof_min_seconds"])
        return None

    clip_id = f"bestof-{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    clip_dir = s["work_dir"] / name / "clips" / clip_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    log.info("[%s] %s: %d Momente von %s (%.0f s) rendern ...", name, todo["label"], len(picks),
             ", ".join(todo["names"]), total)
    parts, helpers = [], []
    for i, (moment, headline) in enumerate(picks, 1):
        words = [Word(a, b, text) for a, b, text in moment["words"]]
        ass = subtitles.write_ass(
            words, 0, moment["duration"], clip_dir / f"teil{i}.ass", s["subtitle_font"], s["subtitle_size"],
            s["subtitle_margin_bottom"], s["subtitle_uppercase"], s["subtitle_words_per_line"],
            hook=headline, hook_seconds=0)
        parts.append(render.render_clip(Path(moment["path"]), 0, moment["duration"], clip_dir / f"teil{i}.mp4",
                                        ass, s["layout"], s["zoom"]))
        helpers.append(ass)
    out = render.concat(parts, clip_dir / f"{clip_id}.mp4")
    for path in parts + helpers:
        path.unlink(missing_ok=True)

    reason = f"{todo['label']} aus {len(picks)} Momenten von {', '.join(todo['names'])}."
    transcript = "\n".join(f"{m['creator_name']}: {m['text']}" for m, _ in picks)
    state.mark_video(clip_id, name, f"{todo['label']} ({time.strftime('%d.%m.%Y')})", "done", kind="bestof")
    state.add_clip(clip_id, clip_id, name, out, todo["title"], todo["caption"], 0, total, todo["platforms"],
                   picks[0][1], reason, "wartet" if s["require_approval"] else "freigegeben", transcript,
                   creator=", ".join(todo["names"]), kind="bestof")
    state.use_moments([m["moment_id"] for m, _ in picks], name, clip_id)
    log.info("[%s] %s fertig (%.0f s): %s", name, todo["label"], total, todo["title"])
    return clip_id
