"""Automatik: Watchlist pruefen, neue Videos clippen, Best-ofs bauen, zu den eingestellten Uhrzeiten hochladen.

Zwei Takte laufen im Hintergrund des Dashboards:
- Watchlist (immer, auch waehrend ein Auftrag arbeitet): Reminder schicken, neue Videos in die Warteschlange stellen.
- Arbeit (nur wenn "Automatik" an ist und gerade kein Auftrag laeuft): faellige Uploads, dann wartende Videos,
  dann faellige Best-ofs. Es laeuft immer nur ein Auftrag gleichzeitig.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from clipbot import bestof, config, lang, notify, schedule, watch
from clipbot.state import State

log = logging.getLogger("clipbot")
TICK_SECONDS = 20


def account_times(account: dict, settings: dict) -> list[tuple[int, int]]:
    """Upload-Uhrzeiten des Kontos: eigene, sonst die allgemeinen."""
    try:
        return schedule.parse_times(account.get("times") or settings["upload_times"])
    except ValueError:
        return []


def due_uploads(cfg: dict, state: State, now: datetime) -> list[tuple[dict, dict, dict]]:
    """Was jetzt nach Zeitplan hochzuladen ist: je Konto mit offenem Zeitfenster der aelteste freigegebene Clip.
    Gibt [(konto, kanal, clip)] zurueck."""
    s = cfg["settings"]
    due, taken = [], set()
    for account in cfg.get("accounts") or []:
        times = account_times(account, s)
        slot = schedule.open_slot(times, now, s["upload_grace_minutes"]) if times else None
        if not slot or state.posts_since(account["id"], slot.timestamp()):
            continue  # kein Zeitfenster offen oder in diesem Fenster schon etwas versucht
        if not config.account_connected(account):
            continue
        best = None
        for channel in config.channels_of_account(cfg, account["id"]):
            exclusive = channel.get("distribute") == "abwechselnd"
            for clip in state.pending_clips(channel["name"], account["id"], account["platform"], exclusive):
                if exclusive and (clip["clip_id"], account["platform"]) in taken:
                    continue  # bekommt in diesem Durchgang schon ein anderes Konto
                if not Path(clip["path"]).exists():
                    continue
                if best is None or clip["ts"] < best[1]["ts"]:
                    best = (channel, clip)
                break
        if best:
            due.append((account, best[0], dict(best[1])))
            taken.add((best[1]["clip_id"], account["platform"]))
    return due


def run_uploads(cfg: dict, due, open_state) -> bool:
    import run as pipeline
    state = open_state()
    try:
        results = [pipeline.post_clip(account, clip, channel, cfg["settings"], state)
                   for account, channel, clip in due]
        return all(results)
    finally:
        state.close()


def run_clip(cfg: dict, creator: dict, video: dict, open_state) -> None:
    """Ein Video aus der Warteschlange clippen (fuer alle Kanaele des Creators)."""
    import run as pipeline
    s = cfg["settings"]
    state = open_state()
    try:
        channels = config.channels_of_creator(cfg, creator["id"])
        try:
            made = pipeline.process_video(channels, s, state, video, creator)
        except Exception as exc:
            state.mark_video(video["id"], "", "", "failed", note=str(exc)[:300])
            notify.send(state, s, "error", f"Clippen fehlgeschlagen: {creator['name']}", str(exc)[:200])
            raise
        if state.video_status(video["id"]) == "queued":
            state.mark_video(video["id"], "", "", "done")
        if made:
            where = "Sie warten unter „Freigabe“ auf dich." if s["require_approval"] else "Sie sind freigegeben."
            notify.send(state, s, "clips", f"{made} neue Clips von {creator['name']}", where)
    finally:
        state.close()


def run_bestof(cfg: dict, channel: dict, period_id: str, mode: str, open_state) -> None:
    s = cfg["settings"]
    state = open_state()
    try:
        state.set_meta(f"bestof:{channel['name']}", period_id)  # je Zeitraum nur ein Versuch
        clip_id = bestof.build(cfg, channel, state, mode)
        if clip_id:
            clip = state.get_clip(clip_id)
            notify.send(state, s, "bestof", f"Best-of fertig: {channel['name']}", clip["title"])
    finally:
        state.close()


def next_task(cfg: dict, open_state, now: datetime | None = None):
    """Die naechste faellige Arbeit als (Bezeichnung, Funktion) oder None."""
    now = now or datetime.now()
    s = cfg["settings"]
    state = open_state()
    try:
        if not s["dry_run"]:
            due = due_uploads(cfg, state, now)
            if due:
                names = ", ".join(a.get("label") or a["id"] for a, _, _ in due)
                return f"Upload nach Zeitplan ({names})", lambda: run_uploads(cfg, due, open_state)

        creators = {c["id"]: c for c in cfg.get("creators") or []}
        for row in state.videos_by_status(("queued",)):
            creator = creators.get(row["creator"])
            if not creator or not watch.wants_clips(cfg, creator):  # Creator entfernt oder Clippen abgeschaltet
                state.mark_video(row["video_id"], "", "", "seen")
                continue
            video = {"id": row["video_id"], "title": row["title"] or row["video_id"], "url": row["url"]}
            return (f"Automatisch clippen: {creator['name']} – {video['title']}"[:140],
                    lambda: run_clip(cfg, creator, video, open_state))

        for channel in cfg["channels"]:
            mode = channel.get("bestof", "off")
            if channel.get("kind", "clips") != "clips" or mode not in ("daily", "weekly"):
                continue
            period_id = schedule.bestof_period(mode, now, s["bestof_time"], s["bestof_weekday"])
            if period_id and state.get_meta(f"bestof:{channel['name']}") != period_id:
                label = lang.LANGS["de"]["bestof_weekly" if mode == "weekly" else "bestof_daily"]
                return (f"{label}: {channel['name']}",
                        lambda: run_bestof(cfg, channel, period_id, mode, open_state))
        return None
    finally:
        state.close()


class Autopilot:
    """Die beiden Hintergrund-Takte des Dashboards."""

    def __init__(self, start_job, is_busy, open_state):
        self.start_job, self.is_busy, self.open_state = start_job, is_busy, open_state
        self.watching = False
        self.last_watch = 0.0
        self._kick = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._watch_loop, daemon=True).start()
        threading.Thread(target=self._work_loop, daemon=True).start()

    def check_now(self) -> None:
        """Die Watchlist sofort pruefen (Knopf im Dashboard)."""
        self._kick.set()

    def watch_once(self) -> float:
        """Ein Durchgang ueber die Watchlist. Gibt die Wartezeit bis zum naechsten in Sekunden zurueck."""
        cfg = config.load(allow_empty=True)
        s = cfg["settings"]
        if cfg.get("creators"):
            self.watching = True
            state = self.open_state(s)
            try:
                watch.check_all(cfg, state)
            finally:
                state.close()
                self.watching = False
            self.last_watch = time.time()
        return max(60.0, float(s["watch_interval_minutes"]) * 60)

    def _watch_loop(self) -> None:
        time.sleep(4)
        while True:
            wait = 300.0
            try:
                wait = self.watch_once()
            except config.ConfigError:
                pass
            except Exception:
                log.exception("Watchlist: Durchgang abgebrochen")
            self._kick.wait(wait)
            self._kick.clear()

    def tick(self, now: datetime | None = None) -> bool:
        """Startet die naechste faellige Arbeit, wenn die Automatik an ist und nichts laeuft."""
        cfg = config.load(allow_empty=True)
        s = cfg["settings"]
        if not s["autopilot"] or self.is_busy():
            return False
        task = next_task(cfg, lambda: self.open_state(s), now)
        return bool(task and self.start_job(*task))

    def _work_loop(self) -> None:
        while True:
            time.sleep(TICK_SECONDS)
            try:
                self.tick()
            except config.ConfigError:
                pass
            except Exception:
                log.exception("Automatik: Takt abgebrochen")
