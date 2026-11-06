#!/usr/bin/env python3
"""ClipBot-Dashboard: private Weboberflaeche, laeuft nur auf diesem PC (http://127.0.0.1:8765)."""
from __future__ import annotations

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
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

import run as pipeline
from clipbot import config
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
tiktok_pending: dict[str, str] = {}  # Kanalname -> state der laufenden Anmeldung


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
    raise ApiError(f"Kanal '{name}' gibt es nicht.", 404)


def account_status(channel: dict) -> dict:
    result = {}
    for platform in PLATFORMS:
        target = (channel.get("upload") or {}).get(platform)
        if not target:
            result[platform] = {"enabled": False, "connected": False}
        elif platform == "instagram":
            token = os.environ.get(str(target.get("token_env") or ""))
            result[platform] = {"enabled": True, "connected": bool(target.get("ig_user_id") and token)}
        else:
            result[platform] = {"enabled": True,
                                "connected": config.resolve(target["token_file"]).exists()}
    return result


def body() -> dict:
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------- Seite und Status
@app.get("/")
def index():
    return send_file(WEB / "index.html")


@app.get("/api/state")
def api_state():
    cfg = config.load(allow_empty=True)
    s = cfg["settings"]
    state = open_state(s)
    try:
        clips = state.clips()
        videos = state.videos()
    finally:
        state.close()
    for clip in clips:
        clip["exists"] = Path(clip["path"]).exists()
        del clip["path"]
    channels = [{**ch, "accounts": account_status(ch)} for ch in cfg["channels"]]
    return jsonify(
        job=job, log=list(log_lines), clips=clips, videos=videos, channels=channels,
        settings={k: s[k] for k in config.DEFAULTS if k != "work_dir"},
        setup={
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "youtube_app": config.youtube_secrets_file().exists(),
            "tiktok_app": all(os.environ.get(k) for k in
                              ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI")),
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
    elif mode in ("make", "upload", "both"):
        label = {"make": "Clips erzeugen", "upload": "Offene Clips hochladen",
                 "both": "Erzeugen und hochladen"}[mode] + f" ({names})"

        def work():
            state = open_state(s)
            ok = True
            for channel in channels:
                try:
                    if mode in ("make", "both"):
                        pipeline.make_clips(channel, s, state)
                    if mode in ("upload", "both"):
                        pipeline.upload_clips(channel, s, state)
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
    platform = body().get("platform")
    if platform not in PLATFORMS:
        raise ApiError("Unbekannte Plattform.")
    cfg = config.load(allow_empty=True)
    s = cfg["settings"]
    state = open_state(s)
    try:
        clip = dict(clip_or_404(state, clip_id))
    finally:
        state.close()
    channel = find_channel(cfg, clip["channel"])
    if platform not in channel["upload"]:
        raise ApiError(f"{platform} ist fuer den Kanal '{channel['name']}' nicht eingerichtet.")

    def work():
        st = open_state(s)
        try:
            remote_id = pipeline.upload_one(platform, clip, channel, s)
            st.mark_upload(clip_id, platform, "ok", remote_id)
            log.info("[%s] %s: hochgeladen (%s) - %s", channel["name"], platform, remote_id, clip["title"])
        except (UploadError, config.ConfigError) as exc:
            st.mark_upload(clip_id, platform, "failed", error=str(exc))
            log.error("[%s] %s: %s", channel["name"], platform, exc)

    if not start_job(f"{platform}: {clip['title']}", work):
        raise ApiError(f"Es laeuft bereits: {job['label']}", 409)
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


@app.post("/api/channels")
def channel_save():
    data = body()
    name = str(data.get("name") or "").strip()
    raw = config.load_raw()
    existing = next((c for c in raw["channels"] if c["name"] == name), None)
    if data.get("is_new") and existing:
        raise ApiError(f"Den Kanal '{name}' gibt es schon.")
    old_upload = (existing or {}).get("upload") or {}
    upload = {}
    wanted = data.get("platforms") or {}
    for platform in ("youtube", "tiktok"):
        if wanted.get(platform):
            upload[platform] = old_upload.get(platform) or {"token_file": f"tokens/{name}_{platform}.json"}
    if wanted.get("instagram"):
        upload["instagram"] = {"ig_user_id": str(data.get("ig_user_id") or "").strip(),
                               "token_env": str(data.get("ig_token_env") or "").strip()}
    channel = {
        "name": name,
        "source_url": str(data.get("source_url") or "").strip(),
        "title_template": str(data.get("title_template") or "{hook} #shorts"),
        "caption_template": str(data.get("caption_template") or "{hook}"),
        "tags": [t.strip() for t in str(data.get("tags") or "").split(",") if t.strip()],
        "upload": upload,
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
    return jsonify(ok=True)


@app.post("/api/channels/<name>/delete")
def channel_delete(name: str):
    raw = config.load_raw()
    raw["channels"] = [c for c in raw["channels"] if c["name"] != name]
    config.save(raw)
    return jsonify(ok=True)


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


# ---------------------------------------------------------------- Konten verbinden
@app.post("/api/connect/youtube")
def connect_youtube():
    cfg = config.load(allow_empty=True)
    channel = find_channel(cfg, str(body().get("channel")))
    target = channel["upload"].get("youtube") or {}
    if not target:
        raise ApiError("YouTube ist fuer diesen Kanal nicht eingerichtet.")
    secrets_file = config.youtube_secrets_file()
    if not secrets_file.exists():
        raise ApiError(f"{secrets_file.name} fehlt im ClipBot-Ordner. Das ist die Zugangsdatei deiner eigenen "
                       "Google-App (Google Cloud Console, OAuth-Client vom Typ Desktop-App). "
                       "Die Schritte stehen in der README unter YouTube.")

    def work():
        from clipbot.uploaders import youtube
        try:
            youtube.authorize(secrets_file, config.resolve(target["token_file"]))
            log.info("[%s] YouTube-Konto verbunden", channel["name"])
        except Exception as exc:
            if "timed out" in str(exc).lower():
                log.error("[%s] YouTube-Anmeldung nicht abgeschlossen: 5 Minuten lang kam keine Zustimmung aus dem "
                          "Browser. Zeigt Google 'Zugriff blockiert', fehlt dein Konto als Testnutzer in der "
                          "Google-App.", channel["name"])
            else:
                log.error("[%s] YouTube-Anmeldung fehlgeschlagen: %s", channel["name"], exc)

    threading.Thread(target=work, daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/connect/tiktok/start")
def connect_tiktok_start():
    from clipbot.uploaders import tiktok
    channel = find_channel(config.load(allow_empty=True), str(body().get("channel")))
    if "tiktok" not in channel["upload"]:
        raise ApiError("TikTok ist fuer diesen Kanal nicht eingerichtet.")
    if not all(os.environ.get(k) for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI")):
        raise ApiError("Die Zugangsdaten deiner TikTok-Entwickler-App fehlen. Kopiere .env.example nach .env und "
                       "trage TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET und TIKTOK_REDIRECT_URI ein. "
                       "Die Schritte stehen in der README unter TikTok.")
    url, state = tiktok.authorize_url(config.env("TIKTOK_CLIENT_KEY"), config.env("TIKTOK_REDIRECT_URI"))
    tiktok_pending[channel["name"]] = state
    return jsonify(url=url)


@app.post("/api/connect/tiktok/finish")
def connect_tiktok_finish():
    from clipbot.uploaders import tiktok
    data = body()
    channel = find_channel(config.load(allow_empty=True), str(data.get("channel")))
    state = tiktok_pending.get(channel["name"])
    if not state:
        raise ApiError("Starte die Anmeldung zuerst mit 'Verbinden'.")
    tiktok.finish_authorize(
        config.env("TIKTOK_CLIENT_KEY"), config.env("TIKTOK_CLIENT_SECRET"), config.env("TIKTOK_REDIRECT_URI"),
        state, str(data.get("redirected") or ""), config.resolve(channel["upload"]["tiktok"]["token_file"]))
    tiktok_pending.pop(channel["name"], None)
    log.info("[%s] TikTok-Konto verbunden", channel["name"])
    return jsonify(ok=True)


@app.post("/api/connect/instagram")
def connect_instagram():
    from clipbot.uploaders import instagram
    cfg = config.load(allow_empty=True)
    channel = find_channel(cfg, str(body().get("channel")))
    target = channel["upload"].get("instagram") or {}
    if not target.get("ig_user_id") or not target.get("token_env"):
        raise ApiError("Trage zuerst die Instagram-User-ID und den Namen der Token-Variable ein.")
    username = instagram.whoami(str(target["ig_user_id"]), config.env(target["token_env"]),
                                cfg["settings"]["instagram_api_version"])
    return jsonify(ok=True, username=username)


# ---------------------------------------------------------------- Start
def main() -> int:
    handler = BufferHandler()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    log.addHandler(handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    url = f"http://127.0.0.1:{PORT}"
    print(f"\nClipBot-Dashboard laeuft: {url}\nDieses Fenster offen lassen. Beenden mit Strg+C.\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
