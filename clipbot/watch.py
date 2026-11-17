"""Watchlist pruefen: Wer ist live, wer hat ein neues Video? Erzeugt Reminder und stellt Videos zum Clippen an.

Ablauf je Creator:
- YouTube: RSS-Feed des Kanals (eine kleine Anfrage). Nur unbekannte Videos werden mit yt-dlp genauer abgefragt.
  Angekuendigte und laufende Streams bleiben unter Beobachtung, bis die Aufzeichnung fertig ist.
- Twitch: laeuft der Kanal gerade? Nach dem Stream kommt die neueste Aufzeichnung in die Warteschlange.
- Andere Seiten (z. B. Kick): nur der Live-Reminder.

Beim ersten Durchgang merkt sich ClipBot nur, was es schon gibt. Reminder kommen erst fuer Neues.
"""
from __future__ import annotations

import logging
import time

from clipbot import config, download, notify, sources
from clipbot.state import WAITING, State

log = logging.getLogger("clipbot")

NEW_MAX_AGE = 3 * 86400        # aeltere Feed-Eintraege gelten nicht als neu (z. B. nach dem Loeschen eines Videos)
UPCOMING_MAX_AGE = 14 * 86400  # so lange wird auf einen angekuendigten Stream gewartet
GONE_MAX_AGE = 3 * 86400       # nicht mehr abrufbare Streams nach dieser Zeit aufgeben


def wants_clips(cfg: dict, creator: dict) -> bool:
    """Sollen Videos dieses Creators automatisch geclippt werden?"""
    if not creator.get("auto_clip", True):
        return False
    return any(ch.get("clips", True) or ch.get("bestof", "off") != "off"
               for ch in config.channels_of_creator(cfg, creator["id"]))


class _Check:
    """Ein Pruefdurchgang fuer einen Creator."""

    def __init__(self, cfg: dict, creator: dict, state: State, now: float):
        self.cfg, self.creator, self.state, self.now = cfg, creator, state, now
        self.id, self.name, self.url = creator["id"], creator.get("name") or creator["id"], creator["url"]
        self.watch = state.watch_get(self.id)
        self.auto = wants_clips(cfg, creator)

    def notify(self, kind: str, title: str, text: str = "", url: str = "") -> None:
        if self.creator.get("notify", True):
            notify.send(self.state, self.cfg["settings"], kind, title, text, url or self.url, self.id)

    def mark(self, video: dict, status: str, kind: str = "") -> None:
        self.state.mark_video(video["id"], "", video.get("title") or "", status, creator=self.id,
                              url=video.get("url") or "", kind=kind)

    def went_live(self, title: str, url: str) -> None:
        self.state.watch_set(self.id, live=1, live_title=title, live_url=url, live_since=self.now)
        self.notify("live", f"{self.name} ist live", title, url)

    def went_offline(self) -> None:
        if self.watch.get("live"):
            self.state.watch_set(self.id, live=0)

    def new_video(self, video: dict, was_stream: bool) -> None:
        """Ein fertiges Video oder eine fertige Aufzeichnung: Reminder und, falls gewuenscht, ab in die Warteschlange."""
        title = video.get("title") or ""
        self.state.watch_set(self.id, last_title=title, last_url=video.get("url") or "", last_ts=self.now)
        text = f"{title} (Clips werden automatisch erstellt)" if self.auto else title
        if was_stream:
            self.notify("video", f"{self.name}: Stream-Aufzeichnung ist da", text, video.get("url") or "")
        else:
            self.notify("video", f"{self.name} hat ein neues Video", text, video.get("url") or "")
        if was_stream and self.auto and self.live_clipped():
            self.state.mark_video(video["id"], "", title, "seen", creator=self.id, url=video.get("url") or "",
                                  kind="live", note="Schon live aus den Chat-Peaks geclippt")
            return
        self.mark(video, "queued" if self.auto else "seen", "live" if was_stream else "video")

    def live_clipped(self) -> bool:
        """Wurde der Stream schon live geclippt (Chat-Peaks)? Dann die Aufzeichnung nicht noch einmal clippen,
        ausser ein Kanal des Creators braucht Momente fuer Best-ofs."""
        last = float(self.state.get_meta(f"live_session:{self.id}") or 0)
        if self.now - last > 12 * 3600:
            return False
        return not any(ch.get("bestof", "off") != "off" for ch in config.channels_of_creator(self.cfg, self.id))

    # ------------------------------------------------------------ YouTube
    def classify(self, video: dict, info: dict, prev: str | None, seen: float) -> None:
        """Ordnet ein Video nach seinem Live-Status ein. prev: bisheriger Zustand (None = gerade entdeckt)."""
        status = info.get("live_status")
        video = {**video, "title": info.get("title") or video.get("title") or ""}
        if status == "is_upcoming":
            if prev is None:
                self.mark(video, "upcoming", "live")
            elif self.now - seen > UPCOMING_MAX_AGE:
                self.mark(video, "skipped", "live")
        elif status == "is_live":
            if prev != "live":
                self.mark(video, "live", "live")
                self.went_live(video["title"], video.get("url") or self.url)
        elif status == "post_live":  # Stream vorbei, YouTube verarbeitet die Aufzeichnung noch
            if prev != "processing":
                self.mark(video, "processing", "live")
            if prev == "live":
                self.went_offline()
        else:
            was_stream = prev in WAITING or status == "was_live"
            if prev in ("live", "processing"):
                self.went_offline()
            duration = info.get("duration") or 0
            if duration and duration < download.MIN_SECONDS:  # Shorts und Trailer: kein Reminder, kein Clip
                self.mark(video, "skipped", "video")
            else:
                self.new_video(video, was_stream)

    def youtube(self) -> None:
        channel_id = self.watch.get("channel_id")
        if not channel_id:
            channel_id = sources.youtube_channel_id(self.url)
            if channel_id:
                self.state.watch_set(self.id, channel_id=channel_id)
        try:
            if not channel_id:
                raise sources.SourceError("Kanal-ID unbekannt")
            entries = sources.youtube_feed(channel_id)
        except Exception as exc:  # Feed gestoert: die Kanalseite direkt lesen (langsamer)
            log.debug("[%s] Feed nicht lesbar (%s), lese die Kanalseite.", self.id, exc)
            entries = sources.list_latest(self.url, 8)

        if not self.watch.get("baseline"):
            for entry in entries:
                if self.state.video_status(entry["id"]) is None:
                    self.mark(entry, "seen")
            try:  # laeuft gerade ein Stream oder ist einer angekuendigt?
                for stream in sources.youtube_streams(self.url):
                    if stream.get("live_status") == "is_live":
                        self.mark(stream, "live", "live")
                        self.went_live(stream["title"], stream["url"])
                    elif stream.get("live_status") == "is_upcoming":
                        self.mark(stream, "upcoming", "live")
            except Exception as exc:
                log.debug("[%s] Streams nicht lesbar: %s", self.id, exc)
            if entries:
                self.state.watch_set(self.id, last_title=entries[0]["title"], last_url=entries[0]["url"],
                                     last_ts=entries[0].get("published") or self.now)
            self.state.watch_set(self.id, baseline=1)
            return

        for entry in reversed(entries):  # aelteste zuerst, damit die Reminder in der richtigen Reihenfolge kommen
            if self.state.video_status(entry["id"]) is not None:
                continue
            if entry.get("published") and self.now - entry["published"] > NEW_MAX_AGE:
                self.mark(entry, "seen")
                continue
            self.classify(entry, sources.probe(entry["url"]), None, self.now)

        for row in self.state.videos_by_status(WAITING, creator=self.id):
            video = {"id": row["video_id"], "title": row["title"], "url": row["url"]}
            seen = row["seen"] or row["ts"] or self.now
            try:
                info = sources.probe(row["url"])
            except sources.SourceError:
                if self.now - seen > GONE_MAX_AGE:  # geloescht oder privat
                    self.mark(video, "skipped", "live")
                    if row["status"] == "live":
                        self.went_offline()
                continue
            self.classify(video, info, row["status"], seen)

    # ------------------------------------------------------------ Twitch
    def twitch(self) -> None:
        live = sources.live_now(self.url)
        if live and not self.watch.get("live"):
            self.went_live(live.get("title") or "", live.get("url") or self.url)
        if not live:
            self.went_offline()
        entries = sources.list_latest(self.url, 4)
        if not self.watch.get("baseline"):
            # Laeuft gerade ein Stream, ist der erste Eintrag dessen wachsende Aufzeichnung: die soll spaeter geclippt werden.
            for entry in entries[1:] if live else entries:
                if self.state.video_status(entry["id"]) is None:
                    self.mark(entry, "seen")
            self.state.watch_set(self.id, baseline=1)
            return
        if live:
            return  # die Aufzeichnung waechst noch
        for entry in reversed(entries):
            if self.state.video_status(entry["id"]) is None:
                self.new_video(entry, was_stream=True)

    # ------------------------------------------------------------ andere Seiten
    def live_only(self) -> None:
        live = sources.live_now(self.url)
        if live and not self.watch.get("live"):
            self.went_live(live.get("title") or "", live.get("url") or self.url)
        if not live:
            self.went_offline()

    def run(self) -> None:
        platform = sources.platform_of(self.url)
        if platform == "youtube":
            self.youtube()
        elif platform == "twitch":
            self.twitch()
        else:
            self.live_only()


def check_creator(cfg: dict, creator: dict, state: State, now: float | None = None) -> bool:
    """Prueft einen Creator. Fehler landen in der Watchlist-Anzeige und brechen nichts ab. True = ohne Fehler."""
    now = now or time.time()
    try:
        _Check(cfg, creator, state, now).run()
        state.watch_set(creator["id"], last_check=now, error="")
        return True
    except Exception as exc:
        state.watch_set(creator["id"], last_check=now, error=str(exc)[:300])
        log.debug("[%s] Watchlist-Pruefung fehlgeschlagen: %s", creator["id"], exc)
        return False


def check_all(cfg: dict, state: State, pause: float = 0.4, stop=None) -> int:
    """Prueft alle Creator mit Reminder oder automatischem Clippen. Gibt die Zahl der geprueften Creator zurueck."""
    count = 0
    for creator in cfg.get("creators") or []:
        if stop is not None and stop():
            break
        if not (creator.get("notify", True) or creator.get("auto_clip", True)):
            continue
        check_creator(cfg, creator, state)
        count += 1
        if pause:
            time.sleep(pause)
    return count
