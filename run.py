#!/usr/bin/env python3
"""ClipBot - ein Lauf: neueste Videos holen -> Highlights finden -> 9:16-Clips mit Untertiteln -> hochladen."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from clipbot import config, download, highlights, render, subtitles, transcribe
from clipbot.state import State
from clipbot.uploaders.base import UploadError

log = logging.getLogger("clipbot")
PLATFORMS = ("youtube", "tiktok", "instagram")


def make_texts(channel: dict, video_title: str, words, start: float, end: float, n: int) -> tuple[str, str]:
    """Titel und Beschreibung aus den Vorlagen bauen; {hook} = erste gesprochene Worte des Clips."""
    spoken = [w.text for w in words if start <= w.start < end]
    hook = " ".join(spoken[:9]).strip(" ,.-")
    if len(hook) > 60:
        hook = hook[:60].rsplit(" ", 1)[0] + "…"
    hook = hook or video_title[:60]
    fields = {"hook": hook, "video_title": video_title, "channel": channel["name"], "n": n}
    return (channel["title_template"].format(**fields).strip()[:100],
            channel["caption_template"].format(**fields).strip())


def process_video(channel: dict, s: dict, state: State, video: dict) -> int:
    """Ein Video herunterladen, Highlights suchen, Clips rendern. video = {"id", "title", "url"}.
    Gibt die Zahl der erzeugten Clips zurueck."""
    name = channel["name"]
    work = s["work_dir"] / name
    log.info("[%s] Lade: %s", name, video["title"])
    try:
        source, info = download.download(video, work / "source", s["max_height"], s["max_video_minutes"])
    except download.Skipped as reason:
        log.info("[%s] Uebersprungen: %s", name, reason)
        if video.get("id"):
            state.mark_video(video["id"], name, video["title"], "skipped")
        return 0

    loud = highlights.loudness_per_second(source)
    windows = highlights.find_windows(
        loud, info["heatmap"], s["clips_per_video"], s["clip_min_seconds"],
        s["clip_max_seconds"], s["skip_start_seconds"], s["skip_end_seconds"])
    log.info("[%s] %d Highlights gefunden%s", name, len(windows),
             " (mit Most-replayed-Daten)" if info["heatmap"] else "")

    for n, (start, end, _score) in enumerate(windows, 1):
        clip_id = f"{info['id']}_{n}"
        clip_dir = work / "clips" / clip_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        log.info("[%s] Clip %d von %d: Sprache erkennen und rendern ...", name, n, len(windows))
        words = transcribe.transcribe_range(source, start - 15, end + 15,
                                            s["whisper_model"], s["language"])
        start, end = highlights.snap_to_speech(words, start, end,
                                               s["clip_min_seconds"], s["clip_max_seconds"])
        end = min(end, info["duration"])
        ass = subtitles.write_ass(
            words, start, end, clip_dir / "subs.ass", s["subtitle_font"], s["subtitle_size"],
            s["subtitle_margin_bottom"], s["subtitle_uppercase"], s["subtitle_words_per_line"])
        out = render.render_clip(source, start, end, clip_dir / f"{clip_id}.mp4", ass,
                                 s["layout"], s["zoom"])
        title, caption = make_texts(channel, info["title"], words, start, end, n)
        state.add_clip(clip_id, info["id"], name, out, title, caption, start, end)
        log.info("[%s] Clip %d fertig (%.0fs): %s", name, n, end - start, title)

    state.mark_video(info["id"], name, info["title"], "done")
    if not s["keep_source_videos"]:
        source.unlink(missing_ok=True)
    return len(windows)


def make_clips(channel: dict, s: dict, state: State) -> int:
    """Die neuesten Videos des Kanals verarbeiten. Gibt die Zahl der neuen Clips zurueck."""
    name = channel["name"]
    videos = download.list_latest(channel["source_url"], s["videos_per_run"])
    todo = [v for v in videos if not state.video_done(v["id"])]
    made = sum(process_video(channel, s, state, video) for video in todo)
    if not todo:
        log.info("[%s] Nichts Neues: Die neuesten %d Videos sind schon verarbeitet oder wurden uebersprungen.",
                 name, len(videos))
    elif not made:
        log.info("[%s] Kein Clip entstanden. Ein bestimmtes Video kannst du ueber 'Einzelnes Video clippen' waehlen.",
                 name)
    else:
        log.info("[%s] %d neue Clips", name, made)
    return made


def upload_one(platform: str, clip, channel: dict, s: dict) -> str:
    target = channel["upload"][platform]
    path = Path(clip["path"])
    if platform == "youtube":
        from clipbot.uploaders import youtube
        return youtube.upload(path, clip["title"], clip["caption"], channel["tags"],
                              config.resolve(target["token_file"]), s["privacy"], s["youtube_category_id"])
    if platform == "tiktok":
        from clipbot.uploaders import tiktok
        return tiktok.upload(path, clip["caption"], config.resolve(target["token_file"]),
                             config.env("TIKTOK_CLIENT_KEY"), config.env("TIKTOK_CLIENT_SECRET"), s["privacy"])
    from clipbot.uploaders import instagram
    return instagram.upload(path, clip["caption"], str(target["ig_user_id"]),
                            config.env(target["token_env"]), s["instagram_api_version"])


def upload_clips(channel: dict, s: dict, state: State) -> None:
    name = channel["name"]
    for platform in PLATFORMS:
        if platform not in channel["upload"]:
            continue
        for clip in state.pending_clips(name, platform)[: s["max_uploads_per_run"]]:
            if not Path(clip["path"]).exists():
                state.mark_upload(clip["clip_id"], platform, "ok", error="Datei fehlt, uebersprungen")
                continue
            try:
                remote_id = upload_one(platform, clip, channel, s)
                state.mark_upload(clip["clip_id"], platform, "ok", remote_id)
                log.info("[%s] %s: hochgeladen (%s) - %s", name, platform, remote_id, clip["title"])
                time.sleep(10)
            except (UploadError, config.ConfigError) as exc:
                state.mark_upload(clip["clip_id"], platform, "failed", error=str(exc))
                log.error("[%s] %s: %s", name, platform, exc)
                break  # Plattform fuer diesen Lauf ueberspringen, naechster Lauf versucht es erneut


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", help="nur diesen Kanal (Name aus config.yaml) verarbeiten")
    parser.add_argument("--dry-run", action="store_true", help="Clips erzeugen, aber nichts hochladen")
    parser.add_argument("--upload-only", action="store_true", help="nur fertige Clips hochladen")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 1
    if not shutil.which("ffmpeg"):
        log.error("ffmpeg wurde nicht gefunden. Bitte installieren (siehe README).")
        return 1

    s = cfg["settings"]
    dry_run = s["dry_run"] or args.dry_run
    state = State(s["work_dir"] / "state.sqlite")
    for channel in cfg["channels"]:
        if args.channel and channel["name"] != args.channel:
            continue
        try:
            if not args.upload_only:
                make_clips(channel, s, state)
            if dry_run:
                log.info("[%s] Testlauf: nichts hochgeladen. Clips liegen in %s",
                         channel["name"], s["work_dir"] / channel["name"] / "clips")
            else:
                upload_clips(channel, s, state)
        except Exception:
            log.exception("[%s] Fehler - weiter mit dem naechsten Kanal", channel["name"])
    log.info("Fertig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
