#!/usr/bin/env python3
"""ClipBot-Dashboard: private Weboberflaeche, laeuft nur auf diesem PC (http://127.0.0.1:8765)."""
from __future__ import annotations

import atexit
import collections
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

import run as pipeline
from clipbot import auto, awake, config, lang, live, render, schedule, sources
from clipbot.state import State
from clipbot.uploaders.base import UploadError

PORT = int(os.environ.get("CLIPBOT_PORT", "8765"))
WEB = Path(__file__).resolve().parent / "clipbot" / "web"
PLATFORMS = pipeline.PLATFORMS
ID_RE = re.compile(r"^[\w-]{1,80}$")

app = Flask(__name__, static_folder=None)
log = logging.getLogger("clipbot")

# ---------------------------------------------------------------- Protokoll und Hintergrund-Auftrag
log_lines: collections.deque = collections.deque(maxlen=400)


class BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1]:
            msg += f" ({record.exc_info[1]})"
        log_lines.append({"t": time.strftime("%H:%M:%S", time.localtime(record.created)),
                          "level": record.levelname, "msg": msg})


job = {"running": False, "label": "", "started": 0.0}
job_lock = threading.Lock()
tiktok_pending: dict[str, str] = {}  # Konto -> state der laufenden Anmeldung


def start_job(label: str, fn) -> bool:
    """Startet fn im Hintergrund. Es laeuft immer nur ein Auftrag gleichzeitig."""
    with job_lock:
        if job["running"]:
            return False
        job.update(running=True, label=label, started=time.time())

    def wrapper():
        try:
            log.info("Start: %s", label)
            if fn() is False:
                log.error("Mit Fehlern beendet: %s. Den Grund nennt die rote Zeile darueber.", label)
            else:
                log.info("Fertig: %s", label)
        except Exception:
            log.exception("Abgebrochen: %s", label)
        finally:
            job["running"] = False

    threading.Thread(target=wrapper, daemon=True).start()
    return True


def _open_state(settings: dict) -> State:
    return State(settings["work_dir"] / "state.sqlite")


autopilot = auto.Autopilot(lambda label, fn: start_job(label, fn), lambda: job["running"], _open_state)
live_manager = live.LiveManager(_open_state)  # Livestreams mitschneiden und bei Chat-Peaks clippen


# ---------------------------------------------------------------- Hilfsfunktionen
class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@app.errorhandler(ApiError)
def _api_error(exc: ApiError):
    return jsonify(error=str(exc)), exc.status


@app.errorhandler(config.ConfigError)
@app.errorhandler(UploadError)
def _known_error(exc):
    return jsonify(error=str(exc)), 400


@app.before_request
def _only_local():
    """Nur Aufrufe von diesem PC und von dieser Seite zulassen (Schutz vor fremden Webseiten)."""
    if request.host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
        abort(403)
    if request.method != "GET" and request.headers.get("X-ClipBot") != "1":
        abort(403)


def open_state(settings: dict) -> State:
    return State(settings["work_dir"] / "state.sqlite")


def find_channel(cfg: dict, name: str) -> dict:
    for ch in cfg["channels"]:
        if ch["name"] == name:
            return ch
    from clipbot.explainer.pipeline import LOCAL_NAME, local_channel
    if name == LOCAL_NAME:  # Erklaervideos ohne Kanal: nur lokal gespeichert, keine Konten
        return local_channel()
    raise ApiError(f"Kanal '{name}' gibt es nicht.", 404)


def find_account(cfg: dict, account_id: str) -> dict:
    for account in cfg["accounts"]:
        if account["id"] == account_id:
            return account
    raise ApiError(f"Das Konto '{account_id}' gibt es nicht.", 404)


def accounts_payload(cfg: dict, state: State, now: datetime) -> list[dict]:
    """Konten fuer das Dashboard: verbunden?, Warteschlange, letzter Upload, naechste Uhrzeit."""
    s = cfg["settings"]
    out = []
    for account in cfg["accounts"]:
        channels = config.channels_of_account(cfg, account["id"])
        queue = sum(len(state.pending_clips(ch["name"], account["id"], account["platform"],
                                            ch.get("distribute") == "abwechselnd")) for ch in channels)
        times = auto.account_times(account, s)
        slot = schedule.next_slot(times, now) if times else None
        last = state.last_post(account["id"])
        out.append({
            "id": account["id"], "platform": account["platform"], "label": account.get("label") or account["id"],
            "times": account.get("times") or "", "effective_times": schedule.format_times(times),
            "ig_user_id": account.get("ig_user_id") or "", "ig_token_env": account.get("token_env") or "",
            "connected": config.account_connected(account), "queue": queue,
            "next_slot": slot.timestamp() if slot else None,
            "last_ts": last["ts"] if last else None, "channels": [ch["name"] for ch in channels],
        })
    return out


def creators_payload(cfg: dict, state: State) -> list[dict]:
    """Watchlist fuer das Dashboard: Creator samt Live-Status und letztem Video."""
    watched = state.watch_all()
    out = []
    for creator in cfg["creators"]:
        w = watched.get(creator["id"], {})
        out.append({
            "id": creator["id"], "name": creator.get("name") or creator["id"], "url": creator["url"],
            "platform": sources.platform_of(creator["url"]),
            "notify": bool(creator.get("notify", True)), "auto_clip": bool(creator.get("auto_clip", True)),
            "channels": [ch["name"] for ch in config.channels_of_creator(cfg, creator["id"])],
            "live": bool(w.get("live")), "live_title": w.get("live_title") or "", "live_url": w.get("live_url") or "",
            "live_since": w.get("live_since"), "last_title": w.get("last_title") or "",
            "last_url": w.get("last_url") or "", "last_ts": w.get("last_ts"), "last_check": w.get("last_check"),
            "error": w.get("error") or "",
        })
    return out


def body() -> dict:
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------- Seite und Status
@app.get("/")
def index():
    return send_file(WEB / "index.html")


def _local_name() -> str:
    from clipbot.explainer.pipeline import LOCAL_NAME
    return LOCAL_NAME


@app.get("/api/state")
def api_state():
    cfg = config.load(allow_empty=True)
    s = cfg["settings"]
    state = open_state(s)
    try:
        clips = state.clips()
        videos = state.videos()
        accounts = accounts_payload(cfg, state, datetime.now())
        creators = creators_payload(cfg, state)
        notifications = state.notifications()
        unread = state.unread_count()
        queued = len(state.videos_by_status(("queued",)))
        moments = state.moment_counts(time.time() - 8 * 86400)
        vault = state.vault_counts()
    finally:
        state.close()
    for clip in clips:
        clip["exists"] = Path(clip["path"]).exists()
        del clip["path"]
    return jsonify(
        job=job, log=list(log_lines), clips=clips, videos=videos, channels=cfg["channels"],
        accounts=accounts, creators=creators, notifications=notifications, unread=unread,
        auto={"enabled": bool(s["autopilot"]), "dry_run": bool(s["dry_run"]), "queued": queued,
              "watching": autopilot.watching, "last_watch": autopilot.last_watch or None, "moments": moments},
        live={"sessions": live_manager.status(), "vault": vault, "vault_size": int(s["vault_size"]),
              "on": bool(s["autopilot"] and s["live_clips"])},
        local_channel=_local_name(), languages=lang.labels(),
        settings={k: s[k] for k in config.DEFAULTS if k != "work_dir"},
        setup={
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "subtitles": render.can_burn_subtitles(),
            "youtube_app": config.youtube_secrets_file().exists(),
            "tiktok_app": all(os.environ.get(k) for k in
                              ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI")),
            "discord": bool(os.environ.get("DISCORD_WEBHOOK_URL")),
        },
    )


# ---------------------------------------------------------------- Laeufe
@app.post("/api/run")
def api_run():
    data = body()
    mode = data.get("mode")
    cfg = config.load(allow_empty=True)
    s = cfg["settings"]
    channels = [find_channel(cfg, data["channel"])] if data.get("channel") else cfg["channels"]
    if not channels:
        raise ApiError("Lege zuerst einen Kanal an.")
    names = ", ".join(c["name"] for c in channels)

    if mode == "video":
        url = str(data.get("url") or "").strip()
        if not url.startswith("https://") or len(channels) != 1:
            raise ApiError("Waehle einen Kanal und gib eine Video-Adresse an, die mit https:// beginnt.")
        label = f"Einzelnes Video clippen ({names})"

        def work():
            state = open_state(s)
            pipeline.process_video(channels[0], s, state, {"id": None, "title": url, "url": url})
    elif mode == "bestof":
        if len(channels) != 1 or channels[0].get("kind", "clips") != "clips":
            raise ApiError("Waehle einen Clip-Kanal fuer das Best-of.")
        period = data.get("period") if data.get("period") in ("daily", "weekly") else None
        label = f"Best-of bauen ({names})"

        def work():
            from clipbot import bestof
            state = open_state(s)
            try:
                bestof.build(cfg, channels[0], state, period, force=True)
            finally:
                state.close()
    elif mode in ("make", "upload", "both"):
        label = {"make": "Clips erzeugen", "upload": "Offene Clips hochladen",
                 "both": "Erzeugen und hochladen"}[mode] + f" ({names})"

        def work():
            state = open_state(s)
            ok = True
            for channel in channels:
                try:
                    if mode in ("make", "both"):
                        pipeline.make_clips(channel, s, state, cfg)
                    if mode in ("upload", "both"):
                        pipeline.upload_clips(channel, s, state, config.accounts_of(cfg, channel))
                except Exception:
                    ok = False
                    log.exception("[%s] Fehler - weiter mit dem naechsten Kanal", channel["name"])
            return ok
    else:
        raise ApiError("Unbekannte Aktion.")

    if not start_job(label, work):
        raise ApiError(f"Es laeuft bereits: {job['label']}", 409)
    return jsonify(ok=True, label=label)


# ---------------------------------------------------------------- Clips
def clip_or_404(state: State, clip_id: str):
    if not ID_RE.match(clip_id):
        abort(404)
    clip = state.get_clip(clip_id)
    if not clip:
        abort(404)
    return clip


def clip_file(clip_id: str) -> Path:
    s = config.load(allow_empty=True)["settings"]
    state = open_state(s)
    try:
        path = Path(clip_or_404(state, clip_id)["path"]).resolve()
    finally:
        state.close()
    if s["work_dir"].resolve() not in path.parents or not path.exists():
        abort(404)
    return path


@app.get("/media/<clip_id>")
def media(clip_id: str):
    return send_file(clip_file(clip_id), mimetype="video/mp4", conditional=True)


@app.get("/thumb/<clip_id>")
def thumb(clip_id: str):
    path = clip_file(clip_id)
    image = path.with_name("thumb.jpg")
    if not image.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=360:-2", str(image)], check=False)
    if not image.exists():
        abort(404)
    return send_file(image, mimetype="image/jpeg", max_age=3600)


@app.post("/api/clips/<clip_id>")
def clip_update(clip_id: str):
    data = body()
    title, caption = str(data.get("title") or "").strip(), str(data.get("caption") or "").strip()
    if not title:
        raise ApiError("Der Titel darf nicht leer sein.")
    state = open_state(config.load(allow_empty=True)["settings"])
    try:
        clip_or_404(state, clip_id)
        state.update_clip(clip_id, title[:100], caption[:2200])
    finally:
        state.close()
    return jsonify(ok=True)


@app.post("/api/clips/<clip_id>/upload")
def clip_upload(clip_id: str):
    cfg = config.load(allow_empty=True)
    s = cfg["settings"]
    state = open_state(s)
    try:
        clip = dict(clip_or_404(state, clip_id))
    finally:
        state.close()
    channel = find_channel(cfg, clip["channel"])
    if channel.get("local"):
        raise ApiError("Dieses Video wurde ohne Kanal erstellt und liegt nur lokal. "
                       "Lege unter „Kanäle“ einen Kanal an, um hochzuladen.")
    account = find_account(cfg, str(body().get("account")))
    if account["id"] not in (channel.get("accounts") or []):
        raise ApiError(f"Das Konto '{account['label']}' gehoert nicht zum Kanal '{channel['name']}'.")

    def work():
        st = open_state(s)
        try:
            st.set_review(clip_id, "freigegeben")  # Wer selbst auf Hochladen klickt, gibt den Clip frei.
            return pipeline.post_clip(account, clip, channel, s, st)
        finally:
            st.close()

    if not start_job(f"{account['label']}: {clip['title']}", work):
        raise ApiError(f"Es laeuft bereits: {job['label']}", 409)
    return jsonify(ok=True)


@app.post("/api/clips/<clip_id>/review")
def clip_review(clip_id: str):
    """Freigeben, verwerfen oder wieder in die Warteschlange stellen."""
    action = body().get("action")
    review = {"approve": "freigegeben", "reject": "verworfen", "reopen": "wartet"}.get(action)
    if not review:
        raise ApiError("Unbekannte Aktion.")
    state = open_state(config.load(allow_empty=True)["settings"])
    try:
        clip_or_404(state, clip_id)
        state.set_review(clip_id, review)
    finally:
        state.close()
    return jsonify(ok=True, review=review)


def _clean_platform_texts(raw) -> dict:
    out = {}
    for platform in PLATFORMS:
        entry = (raw or {}).get(platform) or {}
        title = str(entry.get("title") or "").strip()[:100]
        caption = str(entry.get("caption") or "").strip()[:2200]
        if title or caption:
            out[platform] = {"title": title, "caption": caption}
    return out


@app.post("/api/clips/<clip_id>/texts")
def clip_texts(clip_id: str):
    """Texte je Plattform aus der Freigabe-Ansicht speichern."""
    platforms = _clean_platform_texts(body().get("platforms"))
    if not platforms:
        raise ApiError("Keine Texte uebermittelt.")
    state = open_state(config.load(allow_empty=True)["settings"])
    try:
        clip = clip_or_404(state, clip_id)
        first = platforms.get("youtube") or next(iter(platforms.values()))
        title = first["title"] or clip["title"]
        caption = (platforms.get("tiktok") or first)["caption"] or clip["caption"]
        state.set_texts(clip_id, title, caption, platforms)
    finally:
        state.close()
    return jsonify(ok=True)


@app.post("/api/clips/<clip_id>/retext")
def clip_retext(clip_id: str):
    """Die KI schreibt Titel, Captions, Hashtags und Hook neu (das Video bleibt, wie es ist)."""
    from clipbot import ai, viral
    cfg = config.load(allow_empty=True)
    s = cfg["settings"]
    if not ai.enabled(s):
        raise ApiError("Die KI ist ausgeschaltet (Einstellungen, KI fuer Texte).")
    state = open_state(s)
    try:
        clip = dict(clip_or_404(state, clip_id))
        video = state.db.execute("SELECT title FROM videos WHERE video_id=?", (clip["video_id"],)).fetchone()
    finally:
        state.close()
    channel = find_channel(cfg, clip["channel"])
    transcript = clip.get("transcript") or clip["title"]
    texts = viral.write_texts(s, channel, video["title"] if video else clip["title"], transcript, 1,
                              clip.get("reason") or "", clip.get("creator") or "")
    if not texts["platforms"]:
        raise ApiError("Die KI hat keine brauchbaren Texte geliefert. Details im Protokoll.", 502)
    state = open_state(s)
    try:
        state.set_texts(clip_id, texts["title"], texts["caption"], texts["platforms"])
    finally:
        state.close()
    log.info("[%s] Neue KI-Texte fuer %s. Die Hook-Zeile im Video bleibt (dafuer muesste neu gerendert werden).",
             channel["name"], clip_id)
    return jsonify(ok=True)


@app.post("/api/clips/<clip_id>/delete")
def clip_delete(clip_id: str):
    s = config.load(allow_empty=True)["settings"]
    state = open_state(s)
    try:
        folder = Path(clip_or_404(state, clip_id)["path"]).resolve().parent
        state.delete_clip(clip_id)
    finally:
        state.close()
    if s["work_dir"].resolve() in folder.parents and folder.name == clip_id:
        shutil.rmtree(folder, ignore_errors=True)
    return jsonify(ok=True)


# ---------------------------------------------------------------- Einstellungen und Kanaele
@app.post("/api/settings")
def settings_save():
    data = body()
    raw = config.load_raw()
    for key, default in config.DEFAULTS.items():
        if key == "work_dir" or key not in data:
            continue
        value = data[key]
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, float):
                value = float(value)
            elif key == "language":
                value = str(value).strip() or None
            else:
                value = str(value).strip()
        except (TypeError, ValueError):
            raise ApiError(f"Ungueltiger Wert fuer {key}.")
        raw["settings"][key] = value
    config.save(raw)
    return jsonify(ok=True)


def _ids(value) -> list[str]:
    return [str(v) for v in (value if isinstance(value, list) else []) if str(v)]


@app.post("/api/channels")
def channel_save():
    data = body()
    name = str(data.get("name") or "").strip()
    if data.get("is_new"):
        name = config.slug(name)
        if not name:
            raise ApiError("Gib dem Kanal einen Namen, z. B. deutsche-streamer.")
    with config.LOCK:
        raw = config.load_raw()
        existing = next((c for c in raw["channels"] if c["name"] == name), None)
        if data.get("is_new") and existing:
            raise ApiError(f"Den Kanal '{name}' gibt es schon.")
        kind = "explainer" if data.get("kind") == "explainer" else "clips"
        known_creators = {c["id"] for c in raw["creators"]}
        known_accounts = {a["id"] for a in raw["accounts"]}
        channel = {
            "name": name,
            "kind": kind,
            "language": lang.code(data.get("language"), "de" if kind == "explainer" else "en"),
            "creators": [] if kind == "explainer" else [c for c in _ids(data.get("creators")) if c in known_creators],
            "accounts": [a for a in _ids(data.get("accounts")) if a in known_accounts],
            "title_template": str(data.get("title_template") or "{hook} #shorts"),
            "caption_template": str(data.get("caption_template") or "{hook}"),
            "tags": [t.strip() for t in str(data.get("tags") or "").split(",") if t.strip()],
            "credit": str(data.get("credit") or "").strip(),
            "clips": bool(data.get("clips", True)),
            "bestof": data.get("bestof") if data.get("bestof") in ("daily", "weekly") and kind == "clips" else "off",
            "distribute": "abwechselnd" if data.get("distribute") == "abwechselnd" else "alle",
        }
        for template in (channel["title_template"], channel["caption_template"]):
            try:
                template.format(hook="", video_title="", channel="", n=1)
            except (KeyError, IndexError, ValueError):
                raise ApiError("Vorlagen duerfen nur {hook}, {video_title}, {channel} und {n} enthalten.")
        if existing:
            raw["channels"][raw["channels"].index(existing)] = channel
        else:
            raw["channels"].append(channel)
        config.save(raw)
    return jsonify(ok=True, name=name)


@app.post("/api/channels/<name>/delete")
def channel_delete(name: str):
    with config.LOCK:
        raw = config.load_raw()
        raw["channels"] = [c for c in raw["channels"] if c["name"] != name]
        config.save(raw)
    return jsonify(ok=True)


# ---------------------------------------------------------------- Konten
ACCOUNT_PREFIX = {"youtube": "yt", "tiktok": "tt", "instagram": "ig"}


@app.post("/api/accounts")
def account_save():
    data = body()
    label = str(data.get("label") or "").strip()[:60]
    if not label:
        raise ApiError("Gib dem Konto einen Namen, z. B. den Namen des Kanals auf der Plattform.")
    with config.LOCK:
        raw = config.load_raw()
        account = next((a for a in raw["accounts"] if a["id"] == data.get("id")), None)
        if not account:
            platform = data.get("platform")
            if platform not in config.PLATFORMS:
                raise ApiError("Waehle YouTube, TikTok oder Instagram.")
            account_id = config._unique(f"{ACCOUNT_PREFIX[platform]}_{label.lower()}", {a["id"] for a in raw["accounts"]})
            account = {"id": account_id, "platform": platform}
            if platform != "instagram":
                account["token_file"] = f"tokens/{account_id}.json"
            raw["accounts"].append(account)
        account["label"] = label
        account["times"] = str(data.get("times") or "").strip()
        if account["platform"] == "instagram":
            account["ig_user_id"] = str(data.get("ig_user_id") or "").strip()
            account["token_env"] = str(data.get("ig_token_env") or "").strip()
        wanted = data.get("channels")
        if isinstance(wanted, list):  # Zuordnung zu Kanaelen gleich mit speichern
            for ch in raw["channels"]:
                ids = [a for a in ch.get("accounts") or [] if a != account["id"]]
                ch["accounts"] = ids + [account["id"]] if ch["name"] in wanted else ids
        config.save(raw)
    return jsonify(ok=True, id=account["id"])


@app.post("/api/accounts/<account_id>/delete")
def account_delete(account_id: str):
    with config.LOCK:
        raw = config.load_raw()
        raw["accounts"] = [a for a in raw["accounts"] if a["id"] != account_id]
        for ch in raw["channels"]:
            ch["accounts"] = [a for a in ch.get("accounts") or [] if a != account_id]
        config.save(raw)
    return jsonify(ok=True)


# ---------------------------------------------------------------- Watchlist
def _attach(raw: dict, creator_id: str, wanted) -> None:
    """Den Creator genau den gewuenschten Clip-Kanaelen zuordnen."""
    for ch in raw["channels"]:
        if ch.get("kind", "clips") != "clips":
            continue
        ids = [c for c in ch.get("creators") or [] if c != creator_id]
        ch["creators"] = ids + [creator_id] if ch["name"] in wanted else ids


@app.post("/api/creators")
def creators_add():
    """Einen oder viele Creator auf die Watchlist setzen (eine Adresse je Zeile)."""
    data = body()
    lines = [line.strip() for line in str(data.get("urls") or "").replace(",", "\n").splitlines() if line.strip()]
    if not lines:
        raise ApiError("Gib mindestens eine Adresse an, z. B. https://www.youtube.com/@name oder https://www.twitch.tv/name.")
    wanted = _ids(data.get("channels"))
    single_name = str(data.get("name") or "").strip()[:60] if len(lines) == 1 else ""
    added = skipped = 0
    with config.LOCK:
        raw = config.load_raw()
        for line in lines:
            if not line.startswith("http"):  # nur ein Name: als YouTube-Kanal deuten
                line = "https://www.youtube.com/@" + line.lstrip("@")
            url = sources.clean_url(line.replace("http://", "https://", 1))
            if not url.startswith("https://") or len(url) > 300:
                raise ApiError(f"'{line[:80]}' ist keine gueltige Adresse.")
            creator = next((c for c in raw["creators"] if config._same_url(c.get("url"), url)), None)
            if creator:
                skipped += 1
            else:
                creator = {"id": config._unique(sources.name_from_url(url).lower(), {c["id"] for c in raw["creators"]}),
                           "name": single_name or sources.name_from_url(url), "url": url,
                           "notify": bool(data.get("notify", True)), "auto_clip": bool(data.get("auto_clip", True))}
                raw["creators"].append(creator)
                added += 1
            if wanted:
                for ch in raw["channels"]:
                    if ch["name"] in wanted and ch.get("kind", "clips") == "clips" and creator["id"] not in ch["creators"]:
                        ch["creators"].append(creator["id"])
        config.save(raw)
    autopilot.check_now()
    return jsonify(ok=True, added=added, skipped=skipped)


@app.post("/api/creators/<creator_id>")
def creator_update(creator_id: str):
    data = body()
    with config.LOCK:
        raw = config.load_raw()
        creator = next((c for c in raw["creators"] if c["id"] == creator_id), None)
        if not creator:
            raise ApiError("Diesen Creator gibt es nicht.", 404)
        if "name" in data:
            creator["name"] = str(data["name"]).strip()[:60] or creator["name"]
        for flag in ("notify", "auto_clip"):
            if flag in data:
                creator[flag] = bool(data[flag])
        if isinstance(data.get("channels"), list):
            _attach(raw, creator_id, _ids(data["channels"]))
        config.save(raw)
    return jsonify(ok=True)


@app.post("/api/creators/<creator_id>/delete")
def creator_delete(creator_id: str):
    with config.LOCK:
        raw = config.load_raw()
        raw["creators"] = [c for c in raw["creators"] if c["id"] != creator_id]
        for ch in raw["channels"]:
            ch["creators"] = [c for c in ch.get("creators") or [] if c != creator_id]
        config.save(raw)
    state = open_state(config.load(allow_empty=True)["settings"])
    try:
        state.watch_delete(creator_id)
    finally:
        state.close()
    return jsonify(ok=True)


@app.post("/api/watch/check")
def watch_check():
    autopilot.check_now()
    return jsonify(ok=True)


@app.post("/api/notifications/read")
def notifications_read():
    state = open_state(config.load(allow_empty=True)["settings"])
    try:
        state.mark_read()
    finally:
        state.close()
    return jsonify(ok=True)


@app.post("/api/autopilot")
def autopilot_switch():
    with config.LOCK:
        raw = config.load_raw()
        raw["settings"]["autopilot"] = bool(body().get("on"))
        config.save(raw)
    return jsonify(ok=True, on=raw["settings"]["autopilot"])


# ---------------------------------------------------------------- Kanalsuche
@app.post("/api/search")
def api_search():
    from clipbot import search
    data = body()
    query = str(data.get("query") or "").strip()
    if len(query) < 2:
        raise ApiError("Gib einen Kanalnamen oder ein Thema ein.")
    try:
        if data.get("mode") == "trending":
            results = search.trending_channels(query)
        else:
            results = search.search_channels(query)
    except Exception as exc:
        log.error("Kanalsuche fehlgeschlagen: %s", exc)
        raise ApiError("Die Suche auf YouTube ist fehlgeschlagen. Pruefe die Internetverbindung und "
                       f"aktualisiere yt-dlp (start.bat neu starten). Meldung: {str(exc)[:200]}", 502)
    return jsonify(results=results)


@app.post("/api/ai/test")
def ai_test():
    """Kurzer Probelauf der eingestellten KI (mit den gespeicherten Einstellungen)."""
    from clipbot import ai
    s = config.load(allow_empty=True)["settings"]
    if not ai.enabled(s):
        raise ApiError("Die KI ist ausgeschaltet. Waehle Claude oder Ollama und speichere zuerst.")
    schema = {"type": "object", "properties": {"antwort": {"type": "string"}}, "required": ["antwort"]}
    try:
        data = ai.generate_json(s, "Antworte mit einem kurzen deutschen Satz, dass der Test geklappt hat.", schema)
    except ai.AIError as exc:
        raise ApiError(str(exc), 502)
    return jsonify(ok=True, message=data["antwort"])


# ---------------------------------------------------------------- Erklaervideos
def _split(text: str) -> list[str]:
    return [t.strip() for t in str(text or "").split(",") if t.strip()]


@app.post("/api/explainer/trends")
def explainer_trends():
    """Gerade erfolgreiche kurze Erklaervideos auf YouTube plus eigene Themenideen der KI."""
    from clipbot import ai
    from clipbot.explainer import trends
    s = config.load(allow_empty=True)["settings"]
    data = body()
    query = str(data.get("query") or "").strip()
    language = lang.get(data.get("language"), "de")["prompt"]
    queries = [query] if query else _split(s["explainer_queries"])
    found = trends.find_trending(queries, per_query=20 if query else 12)
    ideas, note = [], ""
    if not ai.enabled(s):
        note = "Fuer eigene Themenideen waehle unter Einstellungen eine KI."
    else:
        try:
            ideas = trends.ideas(s, found, _split(s["explainer_themes"]), language=language)
        except ai.AIError as exc:
            note = f"Keine Ideen von der KI: {exc}"
    return jsonify(ok=True, trending=found, ideas=ideas, note=note)


@app.post("/api/explainer/create")
def explainer_create():
    from clipbot import ai
    from clipbot.explainer import pipeline as explainer
    data = body()
    cfg = config.load(allow_empty=True)
    s = cfg["settings"]
    name = str(data.get("channel") or "").strip()
    if name:
        channel = find_channel(cfg, name)
    else:  # Kein Kanal gewaehlt oder angelegt: Video trotzdem bauen, es bleibt lokal.
        from clipbot.explainer.pipeline import local_channel
        channel = local_channel()
    topic = str(data.get("topic") or "").strip()[:200]
    hook = str(data.get("hook") or "").strip()[:200]
    if len(topic) < 4:
        raise ApiError("Gib ein Thema an, z. B. „Was passiert, wenn du Plastik verschluckst?“")
    if not ai.enabled(s):
        raise ApiError("Fuer Erklaervideos muss unter Einstellungen eine KI (Claude oder Ollama) gewaehlt sein.")

    def work():
        state = open_state(s)
        try:
            explainer.create(s, channel, state, topic, hook)
        finally:
            state.close()

    label = f"Erklaervideo: {topic}"
    if not start_job(label, work):
        raise ApiError(f"Es laeuft bereits: {job['label']}", 409)
    return jsonify(ok=True, label=label)


@app.post("/api/vault/<name>/release")
def vault_release(name: str):
    """Den Vault eines Kanals sofort freigeben (ohne auf die volle Zahl zu warten)."""
    cfg = config.load(allow_empty=True)
    find_channel(cfg, name)
    state = open_state(cfg["settings"])
    try:
        count = state.release_vault(name)
    finally:
        state.close()
    if not count:
        raise ApiError("Im Vault dieses Kanals liegt gerade nichts.")
    log.info("[%s] Vault von Hand freigegeben: %d Live-Clips.", name, count)
    return jsonify(ok=True, released=count)


@app.post("/api/quit")
def quit_app():
    """ClipBot beenden (fuer den Start per Mac-App ohne Terminalfenster)."""
    if job["running"]:
        raise ApiError(f"Es laeuft noch: {job['label']}. Warte, bis es fertig ist.", 409)
    live_manager.stop_all()  # ffmpeg-Mitschnitte beenden
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return jsonify(ok=True)


# ---------------------------------------------------------------- Konten verbinden
def _account_for(platform: str) -> tuple[dict, dict]:
    cfg = config.load(allow_empty=True)
    account = find_account(cfg, str(body().get("account")))
    if account["platform"] != platform:
        raise ApiError(f"Das Konto '{account['label']}' ist kein {platform}-Konto.")
    return cfg, account


@app.post("/api/connect/youtube")
def connect_youtube():
    _, account = _account_for("youtube")
    secrets_file = config.youtube_secrets_file()
    if not secrets_file.exists():
        raise ApiError(f"{secrets_file.name} fehlt im ClipBot-Ordner. Das ist die Zugangsdatei deiner eigenen "
                       "Google-App (Google Cloud Console, OAuth-Client vom Typ Desktop-App). "
                       "Die Schritte stehen in der README unter YouTube.")
    label = account["label"]

    def work():
        from clipbot.uploaders import youtube
        try:
            youtube.authorize(secrets_file, config.resolve(account["token_file"]))
            log.info("[%s] YouTube-Konto verbunden", label)
        except Exception as exc:
            if "timed out" in str(exc).lower():
                log.error("[%s] YouTube-Anmeldung nicht abgeschlossen: 5 Minuten lang kam keine Zustimmung aus dem "
                          "Browser. Zeigt Google 'Zugriff blockiert', fehlt dein Konto als Testnutzer in der "
                          "Google-App.", label)
            else:
                log.error("[%s] YouTube-Anmeldung fehlgeschlagen: %s", label, exc)

    threading.Thread(target=work, daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/connect/tiktok/start")
def connect_tiktok_start():
    from clipbot.uploaders import tiktok
    _, account = _account_for("tiktok")
    if not all(os.environ.get(k) for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI")):
        raise ApiError("Die Zugangsdaten deiner TikTok-Entwickler-App fehlen. Kopiere .env.example nach .env und "
                       "trage TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET und TIKTOK_REDIRECT_URI ein. "
                       "Die Schritte stehen in der README unter TikTok.")
    url, state = tiktok.authorize_url(config.env("TIKTOK_CLIENT_KEY"), config.env("TIKTOK_REDIRECT_URI"))
    tiktok_pending[account["id"]] = state
    return jsonify(url=url)


@app.post("/api/connect/tiktok/finish")
def connect_tiktok_finish():
    from clipbot.uploaders import tiktok
    _, account = _account_for("tiktok")
    state = tiktok_pending.get(account["id"])
    if not state:
        raise ApiError("Starte die Anmeldung zuerst mit 'Verbinden'.")
    tiktok.finish_authorize(
        config.env("TIKTOK_CLIENT_KEY"), config.env("TIKTOK_CLIENT_SECRET"), config.env("TIKTOK_REDIRECT_URI"),
        state, str(body().get("redirected") or ""), config.resolve(account["token_file"]))
    tiktok_pending.pop(account["id"], None)
    log.info("[%s] TikTok-Konto verbunden", account["label"])
    return jsonify(ok=True)


@app.post("/api/connect/instagram")
def connect_instagram():
    from clipbot.uploaders import instagram
    cfg, account = _account_for("instagram")
    if not account.get("ig_user_id") or not account.get("token_env"):
        raise ApiError("Trage zuerst die Instagram-User-ID und den Namen der Token-Variable ein.")
    username = instagram.whoami(str(account["ig_user_id"]), config.env(account["token_env"]),
                                cfg["settings"]["instagram_api_version"])
    return jsonify(ok=True, username=username)


# ---------------------------------------------------------------- Start
def main() -> int:
    handler = BufferHandler()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    log.addHandler(handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    render.use_full_ffmpeg()
    awake.keep_awake()  # sonst schlaeft der Rechner ein und die Live-Mitschnitte reissen ab
    if shutil.which("ffmpeg") and not render.can_burn_subtitles():
        log.warning(render.SUBTITLE_HINT)
    url = f"http://127.0.0.1:{PORT}"
    print(f"\nClipBot-Dashboard laeuft: {url}\nDieses Fenster offen lassen. Beenden mit Strg+C.\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    autopilot.start()  # Watchlist und Zeitplan im Hintergrund
    live_manager.start()
    atexit.register(live_manager.stop_all)
    app.run(host="127.0.0.1", port=PORT, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
