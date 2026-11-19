#!/usr/bin/env python3
"""ClipBot - ein Lauf: neueste Videos holen -> Highlights finden -> 9:16-Clips mit Untertiteln -> hochladen."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from clipbot import ai, bestof, campaigns, config, download, genre, highlights, notify, render, subtitles, transcribe, viral
from clipbot.state import State
from clipbot.uploaders.base import UploadError

log = logging.getLogger("clipbot")
PLATFORMS = config.PLATFORMS
UPLOAD_PAUSE = 10  # Sekunden zwischen zwei Uploads


def make_texts(channel: dict, video_title: str, words, start: float, end: float, n: int) -> tuple[str, str]:
    """Titel und Beschreibung aus den Vorlagen (ohne KI); {hook} = erster gesprochener Satz des Clips."""
    texts = viral._template_texts(channel, video_title, viral.spoken_text(words, start, end), n)
    return texts["title"], texts["caption"]


def candidate_windows(source, info: dict, s: dict, loud=None) -> list[dict]:
    """Moegliche Clip-Stellen. Mit KI werden mehr Kandidaten transkribiert und von der KI bewertet."""
    use_ai = ai.enabled(s)
    count = max(s["clips_per_video"], s["ai_candidates"]) if use_ai else s["clips_per_video"]
    if loud is None:
        loud = highlights.loudness_per_second(source)
    windows = highlights.find_windows(
        loud, info["heatmap"], count, s["clip_min_seconds"],
        s["clip_max_seconds"], s["skip_start_seconds"], s["skip_end_seconds"])
    candidates = []
    for i, (start, end, score) in enumerate(windows, 1):
        if use_ai:
            log.info("Kandidat %d von %d: Sprache erkennen ...", i, len(windows))
        words = transcribe.transcribe_range(source, start - 15, end + 15, s["whisper_model"], s["language"])
        candidates.append({"start": start, "end": end, "score": score, "words": words,
                           "text": viral.spoken_text(words, start, end)})
        if not use_ai and len(candidates) >= s["clips_per_video"]:
            break
    if not use_ai:
        return candidates
    return viral.pick_highlights(s, candidates, s["clips_per_video"], info["title"])


def render_picks(channel: dict, s: dict, state: State, source, info: dict, picks: list[dict],
                 creator_name: str, suffix: str = "", review: str | None = None, kind: str = "clip",
                 campaign: dict | None = None) -> int:
    """Rendert die gewaehlten Stellen als Clips dieses Kanals (Texte in der Sprache des Kanals).
    campaign: Clip-Kampagne des Creators; dann bekommt jeder Clip ihre Hashtags und wird sofort gepostet."""
    name = channel["name"]
    work = s["work_dir"] / name
    for n, pick in enumerate(picks, 1):
        clip_id = f"{info['id']}_{n}{suffix}"
        clip_dir = work / "clips" / clip_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        words = pick["words"]
        if pick.get("reason"):
            log.info("[%s] Clip %d (KI-Wertung %s): %s", name, n, pick.get("viral_score"), pick["reason"])
        start, end = highlights.snap_to_speech(words, pick["start"], pick["end"],
                                               s["clip_min_seconds"], s["clip_max_seconds"])
        end = min(end, info["duration"])
        category = genre.category_from_info(info, start)
        kind_of = genre.classify(category, info["title"]) if s.get("genre_formats", True) else None
        cs = genre.settings_for(s, kind_of)  # Bildaufbau je Art
        texts = viral.write_texts(s, channel, info["title"], viral.spoken_text(words, start, end), n,
                                  pick.get("reason", ""), creator_name, kind_of, category)
        if campaign:
            texts = campaigns.apply_tags(texts, campaign)
        hook = texts["hook_overlay"] if s["hook_overlay"] else ""
        log.info("[%s] Clip %d von %d: rendern ...", name, n, len(picks))
        layout, cam = render.prepare_layout(source, start, end, cs["layout"])
        ass = subtitles.write_ass(
            words, start, end, clip_dir / "subs.ass", s["subtitle_font"], s["subtitle_size"],
            s["subtitle_margin_bottom"], s["subtitle_uppercase"], s["subtitle_words_per_line"], hook=hook,
            hook_seconds=float(s.get("hook_seconds") or 0), hook_margin=render.hook_margin(layout, cam))
        out = render.render_clip(source, start, end, clip_dir / f"{clip_id}.mp4", ass,
                                 layout, s["zoom"], bool(s.get("punch_ins", True)), cam)
        state.add_clip(clip_id, info["id"], name, out, texts["title"], texts["caption"], start, end,
                       texts["platforms"], hook, pick.get("reason", ""),
                       "freigegeben" if campaign else review or ("wartet" if s["require_approval"] else "freigegeben"),
                       viral.spoken_text(words, start, end), creator=creator_name,
                       kind="campaign" if campaign else kind, genre=genre.describe(kind_of, category),
                       campaign=campaign["id"] if campaign else "")
        log.info("[%s] Clip %d fertig (%.0fs): %s", name, n, end - start, texts["title"])
    return len(picks)


def process_video(channels, s: dict, state: State, video: dict, creator: dict | None = None,
                  campaign: dict | None = None) -> int:
    """Ein Video herunterladen, Highlights suchen und fuer jeden Kanal Clips rendern. video = {"id", "title", "url"}.
    channels: ein Kanal oder mehrere (alle Kanaele, zu denen der Creator gehoert).
    Gibt die Zahl der erzeugten Clips zurueck."""
    channels = [channels] if isinstance(channels, dict) else list(channels)
    first = channels[0]
    name = first["name"]
    creator_id = (creator or {}).get("id", "")
    log.info("[%s] Lade: %s", name, video["title"])
    try:
        source, info = download.download(video, s["work_dir"] / name / "source", s["max_height"],
                                         s["max_video_minutes"])
    except download.Skipped as reason:
        log.info("[%s] Uebersprungen: %s", name, reason)
        if video.get("id"):
            state.mark_video(video["id"], name, video["title"], "skipped", creator=creator_id, note=str(reason))
        return 0
    creator_name = str((creator or {}).get("name") or first.get("credit") or info.get("uploader") or "").strip()
    wants_clips = [ch for ch in channels if ch.get("clips", True)]
    wants_moments = any(ch.get("bestof", "off") != "off" for ch in channels)
    loud = highlights.loudness_per_second(source)

    made = 0
    if wants_clips:
        picks = candidate_windows(source, info, s, loud)
        log.info("[%s] %d Highlights gewaehlt%s%s", name, len(picks),
                 " (mit Most-replayed-Daten)" if info["heatmap"] else "",
                 " (von der KI bewertet)" if ai.enabled(s) else "")
        for i, channel in enumerate(wants_clips):
            made += render_picks(channel, s, state, source, info, picks, creator_name,
                                 "" if i == 0 else f"-{channel['name']}", campaign=campaign)
    if wants_moments:
        count = bestof.collect_moments(source, info, s, state, creator_id or name, creator_name or name, loud)
        log.info("[%s] %d kurze Momente fuer Best-ofs gemerkt.", name, count)

    state.mark_video(info["id"], name, info["title"], "done", creator=creator_id)
    if video.get("id") and video["id"] != info["id"]:  # z. B. Twitch: Listen-ID "v123", Video-ID "123"
        state.mark_video(video["id"], name, info["title"], "done", creator=creator_id)
    if not s["keep_source_videos"]:
        source.unlink(missing_ok=True)
    return made


def process_livecut(channels, s: dict, state: State, row, creator: dict, campaign: dict | None = None) -> int:
    """Eine ausgeschnittene Live-Szene (Chat-Peak) als Clip rendern; er landet im Vault des Kanals.
    Mit Clip-Kampagne wird jede Szene ein eigener Clip, der sofort gepostet wird (kein Zusammenschnitt).
    row: Eintrag aus der Warteschlange (videos, kind "livecut"). Gibt die Zahl der Clips zurueck."""
    channels = [ch for ch in ([channels] if isinstance(channels, dict) else list(channels)) if ch.get("clips", True)]
    raw = Path(row["url"] or "")
    note = json.loads(row["note"] or "{}")
    cut_id = row["video_id"]
    if not channels or not raw.exists():
        state.mark_video(cut_id, "", "", "skipped", note="Szene fehlt" if channels else "Kein Kanal fuer Clips")
        return 0
    creator_name = str(creator.get("name") or "").strip()
    if s.get("live_compilation", True) and not campaign:
        return _livecut_to_moment(channels, s, state, raw, note, cut_id, creator, creator_name)
    start, end = float(note.get("start", 0)), float(note.get("end", 0))
    length = end - start
    live_s = {**s, "clip_min_seconds": max(5.0, length - 12), "clip_max_seconds": length + 15}
    words = transcribe.transcribe_range(raw, start - 10, end + 10, s["whisper_model"], s["language"])
    laughs = int(note.get("laughs") or 0)
    reason = (f"Chat-Peak im Livestream: {note.get('score', 0):.0f} statt sonst {note.get('base', 0):.0f}"
              + (f", {laughs}x Lachen im Chat" if laughs else "")) if note.get("mode") != "laut" \
        else "Lauter Moment im Livestream (Chat war nicht lesbar)"
    if note.get("chat"):
        reason += f". Der Chat schrieb: {str(note['chat'])[:300]}"
    info = {"id": cut_id, "title": note.get("stream_title") or row["title"] or creator_name,
            "duration": float(note.get("duration") or end + 10), "heatmap": [],
            "categories": [note["category"]] if note.get("category") else []}
    if render.still_seconds(raw, start, end) > 0.3 * max(1.0, length):
        log.info("[live] Szene verworfen: vor allem Standbild (Werbepause oder Pausenbild).")
        state.mark_video(cut_id, "", "", "skipped", note="Standbild (Werbepause)")
        if not s["keep_source_videos"]:
            raw.unlink(missing_ok=True)
        return 0
    pick = {"start": start, "end": end, "words": words, "reason": reason}
    made = 0
    for i, channel in enumerate(channels):
        made += render_picks(channel, live_s, state, raw, info, [pick], creator_name,
                             "" if i == 0 else f"-{channel['name']}", review="vault", kind="live",
                             campaign=campaign)
    state.mark_video(cut_id, "", "", "done")
    if not s["keep_source_videos"]:
        raw.unlink(missing_ok=True)
    return made


def _livecut_to_moment(channels, s: dict, state: State, raw: Path, note: dict, cut_id: str, creator: dict,
                       creator_name: str) -> int:
    """Zusammenschnitt-Modus: Aus der Szene wird ein kurzer Moment. Reichen die Momente des Streamers fuer
    einen Clip ueber 1 Minute, setzt ClipBot die besten zusammen und legt den Clip in den Vault."""
    moment = bestof.add_live_moment(s, state, raw, note, cut_id, creator["id"], creator_name or creator["id"])
    state.mark_video(cut_id, "", "", "done")
    if not s["keep_source_videos"]:
        raw.unlink(missing_ok=True)
    if not moment:
        return 0
    made = 0
    for channel in channels:
        waiting = bestof.live_moments(state, channel["name"], creator["id"])
        total = sum(m["duration"] for m in waiting)
        clip_id = bestof.build_live(s, channel, state, creator["id"], creator_name or creator["id"],
                                    note.get("category") or None)
        if clip_id:
            made += 1
        else:
            log.info("[%s] %s: Moment gemerkt (%.0f s), gesammelt %d Momente mit %.0f s. Der Clip entsteht ab "
                     "etwa %d s.", channel["name"], creator_name, moment["duration"], len(waiting), total,
                     int(s["live_compilation_min_seconds"]) + int(s["live_moment_seconds"]))
    return made


def make_clips(channel: dict, s: dict, state: State, cfg: dict | None = None) -> int:
    """Die neuesten Videos aller Creator des Kanals verarbeiten. Gibt die Zahl der neuen Clips zurueck."""
    name = channel["name"]
    if channel.get("kind") == "explainer":
        log.info("[%s] Erklaervideo-Kanal: Videos entstehen im Bereich 'Erklaervideos'.", name)
        return 0
    cfg = cfg or {"creators": [], "channels": [channel]}
    creators = config.creators_of(cfg, channel)
    if not creators:
        log.info("[%s] Dem Kanal ist noch kein Creator zugeordnet (Bereich 'Kanaele').", name)
        return 0
    made = found = 0
    for creator in creators:
        try:
            videos = download.list_latest(creator["url"], s["videos_per_run"])
        except Exception as exc:
            log.error("[%s] %s: Die Videoliste konnte nicht geladen werden (%s).", name, creator["name"], exc)
            continue
        todo = [v for v in videos if not state.video_done(v["id"])]
        found += len(todo)
        targets = config.channels_of_creator(cfg, creator["id"]) or [channel]
        for video in todo:
            made += process_video(targets, s, state, video, creator)
    if not found:
        log.info("[%s] Nichts Neues: Die neuesten Videos sind schon verarbeitet oder wurden uebersprungen.", name)
    elif not made:
        log.info("[%s] Kein Clip entstanden. Ein bestimmtes Video kannst du ueber 'Einzelnes Video clippen' waehlen.",
                 name)
    else:
        log.info("[%s] %d neue Clips", name, made)
    return made


def upload_one(account: dict, clip, s: dict, tags: list[str] | None = None, tiktok: dict | None = None) -> str:
    """Laedt einen Clip mit diesem Konto hoch und gibt die Kennung des Beitrags zurueck.
    tiktok: Auswahl aus dem TikTok-Fenster (Sichtbarkeit, Kommentare, ...); ohne gelten die Einstellungen."""
    platform = account["platform"]
    path = Path(clip["path"])
    title, caption = State.texts_for(clip, platform)
    if platform == "youtube":
        from clipbot.uploaders import youtube
        return youtube.upload(path, title, caption, list(tags or []),
                              config.resolve(account["token_file"]), s["privacy"], s["youtube_category_id"])
    if platform == "tiktok":
        from clipbot.uploaders import tiktok
        return tiktok.upload(path, caption, config.resolve(account["token_file"]),
                             config.env("TIKTOK_CLIENT_KEY"), config.env("TIKTOK_CLIENT_SECRET"), s["privacy"],
                             **({"chosen": tiktok} if tiktok is not None else {}))
    from clipbot.uploaders import instagram
    return instagram.upload(path, caption, str(account["ig_user_id"]),
                            config.env(account["token_env"]), s["instagram_api_version"])


def post_clip(account: dict, clip, channel: dict, s: dict, state: State, tiktok: dict | None = None) -> bool:
    """Ein Upload samt Buchfuehrung und Protokoll. True = hochgeladen."""
    name, label = channel["name"], account.get("label") or account["id"]
    try:
        remote_id = upload_one(account, clip, s, channel.get("tags"), **({"tiktok": tiktok} if tiktok else {}))
    except (UploadError, config.ConfigError) as exc:
        state.mark_post(clip["clip_id"], account["id"], account["platform"], "failed", error=str(exc))
        log.error("[%s] %s (%s): %s", name, label, account["platform"], exc)
        notify.send(state, s, "error", f"Upload fehlgeschlagen: {label}", str(exc)[:200])
        return False
    state.mark_post(clip["clip_id"], account["id"], account["platform"], "ok", remote_id)
    log.info("[%s] %s (%s): hochgeladen (%s) - %s", name, label, account["platform"], remote_id, clip["title"])
    notify.send(state, s, "upload", f"Hochgeladen auf {label}", clip["title"])
    return True


def upload_clips(channel: dict, s: dict, state: State, accounts: list[dict] | None = None) -> None:
    """Freigegebene Clips des Kanals mit seinen Konten hochladen (sofort, ohne Zeitplan).
    Die Konten kommen reihum dran; bei der Verteilung 'abwechselnd' bekommt jedes Konto andere Clips."""
    name = channel["name"]
    exclusive = channel.get("distribute") == "abwechselnd"
    active = list(accounts or [])
    done = {a["id"]: 0 for a in active}
    while active:
        for account in list(active):
            label = account.get("label") or account["id"]
            pending = state.pending_clips(name, account["id"], account["platform"], exclusive)
            missing = [c for c in pending if not Path(c["path"]).exists()]
            for clip in missing:
                state.mark_post(clip["clip_id"], account["id"], account["platform"], "ok",
                                error="Datei fehlt, uebersprungen")
            pending = [c for c in pending if c not in missing]
            if not pending or done[account["id"]] >= s["max_uploads_per_run"]:
                if not done[account["id"]]:
                    log.info("[%s] %s: kein freigegebener Clip offen.", name, label)
                active.remove(account)
                continue
            done[account["id"]] += 1
            if not post_clip(account, pending[0], channel, s, state):
                active.remove(account)  # Konto fuer diesen Lauf ueberspringen, der naechste Lauf versucht es erneut
            elif UPLOAD_PAUSE:
                time.sleep(UPLOAD_PAUSE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", help="nur diesen Kanal (Name aus config.yaml) verarbeiten")
    parser.add_argument("--dry-run", action="store_true", help="Clips erzeugen, aber nichts hochladen")
    parser.add_argument("--upload-only", action="store_true", help="nur fertige Clips hochladen")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    render.use_full_ffmpeg()
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
                make_clips(channel, s, state, cfg)
            if dry_run:
                log.info("[%s] Testlauf: nichts hochgeladen. Clips liegen in %s",
                         channel["name"], s["work_dir"] / channel["name"] / "clips")
            else:
                upload_clips(channel, s, state, config.accounts_of(cfg, channel))
        except Exception:
            log.exception("[%s] Fehler - weiter mit dem naechsten Kanal", channel["name"])
    log.info("Fertig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
