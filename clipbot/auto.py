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

from clipbot import bestof, campaigns, config, lang, notify, schedule, watch
from clipbot.state import State

log = logging.getLogger("clipbot")
TICK_SECONDS = 20
QUEUE_MAX_AGE = 3 * 86400  # aeltere wartende Videos nicht mehr clippen (z. B. wenn die Automatik lange aus war)
VAULT_RETRY_SECONDS = 6 * 3600  # nach einem gescheiterten Vault-Upload (z. B. YouTube-Tageslimit) so lange warten


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


def release_vaults(cfg: dict, state: State) -> list[tuple[str, int]]:
    """Kanaele, deren Vault voll ist, freigeben. Gibt [(kanal, zahl)] zurueck."""
    size = int(cfg["settings"]["vault_size"])
    if size <= 0:
        return []
    released = []
    for channel, count in state.vault_counts().items():
        if count >= size:
            released.append((channel, state.release_vault(channel)))
    return released


def vault_due(cfg: dict, state: State, now: float) -> list[tuple[dict, dict, list]]:
    """Freigegebene Live-Clips, die ein Konto noch nicht gepostet hat: [(konto, kanal, [clips])].
    Ein Konto, dessen Upload gerade gescheitert ist, kommt erst nach VAULT_RETRY_SECONDS wieder dran."""
    due = []
    for account in cfg.get("accounts") or []:
        if not config.account_connected(account) or state.failed_posts_since(account["id"], now - VAULT_RETRY_SECONDS):
            continue
        for channel in config.channels_of_account(cfg, account["id"]):
            exclusive = channel.get("distribute") == "abwechselnd"
            clips = [dict(c) for c in state.pending_clips(channel["name"], account["id"], account["platform"], exclusive)
                     if (c["kind"] or "") == "live" and Path(c["path"]).exists()]
            if clips:
                due.append((account, channel, clips))
    return due


def run_vault(cfg: dict, due, open_state) -> bool:
    """Alle faelligen Live-Clips sofort nacheinander posten; scheitert ein Upload, ist das Konto fuer diesen Lauf fertig."""
    import run as pipeline
    s = cfg["settings"]
    state = open_state()
    ok = True
    try:
        for account, channel, clips in due:
            for i, clip in enumerate(clips):
                if not pipeline.post_clip(account, clip, channel, s, state):
                    ok = False
                    break
                if pipeline.UPLOAD_PAUSE and i < len(clips) - 1:
                    time.sleep(pipeline.UPLOAD_PAUSE)
        return ok
    finally:
        state.close()


def run_livecut(cfg: dict, creator: dict, row: dict, open_state) -> None:
    """Eine Live-Szene rendern (fuer alle Clip-Kanaele des Creators)."""
    import run as pipeline
    s = cfg["settings"]
    state = open_state()
    try:
        channels = config.channels_of_creator(cfg, creator["id"])
        try:
            campaign = campaigns.of_creator(cfg, creator["id"])
            made = pipeline.process_livecut(channels, s, state, row, creator, **({"campaign": campaign} if campaign else {}))
        except Exception as exc:
            state.mark_video(row["video_id"], "", "", "failed", note=str(exc)[:300])
            raise
        if made and campaigns.of_creator(cfg, creator["id"]):
            log.info("[Kampagnen] %s: neuer Clip, wird sofort gepostet.", creator["name"])
        elif made:
            counts = state.vault_counts()
            size = int(s["vault_size"])
            for channel in channels:
                if channel["name"] in counts:
                    log.info("[%s] Vault: %d von %d Live-Clips", channel["name"], counts[channel["name"]], size)
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
            campaign = campaigns.of_creator(cfg, creator["id"])
            made = pipeline.process_video(channels, s, state, video, creator, **({"campaign": campaign} if campaign else {}))
        except Exception as exc:
            state.mark_video(video["id"], "", "", "failed", note=str(exc)[:300])
            notify.send(state, s, "error", f"Clippen fehlgeschlagen: {creator['name']}", str(exc)[:200])
            raise
        if state.video_status(video["id"]) == "queued":
            state.mark_video(video["id"], "", "", "done")
        if made:
            many = made != 1
            if campaigns.of_creator(cfg, creator["id"]):
                where = "Sie gehören zur Clip-Kampagne und werden sofort gepostet." if many \
                    else "Er gehört zur Clip-Kampagne und wird sofort gepostet."
            elif s["require_approval"]:
                where = "Sie warten unter „Freigabe“ auf dich." if many else "Er wartet unter „Freigabe“ auf dich."
            else:
                where = "Sie sind freigegeben." if many else "Er ist freigegeben."
            count = f"{made} neue Clips" if many else "1 neuer Clip"
            notify.send(state, s, "clips", f"{count} von {creator['name']}", where)
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
        for channel, count in release_vaults(cfg, state):
            log.info("[%s] Vault voll: %d Live-Clips werden jetzt gepostet.", channel, count)
            notify.send(state, s, "upload", f"Vault voll: {channel}",
                        f"{count} Live-Clips werden jetzt auf allen Konten des Kanals gepostet."
                        if not s["dry_run"] else f"{count} Live-Clips sind freigegeben (Probemodus: kein Upload).")
        if not s["dry_run"]:
            vault = vault_due(cfg, state, now.timestamp())
            if vault:
                count = sum(len(clips) for _, _, clips in vault)
                names = ", ".join(sorted({a.get("label") or a["id"] for a, _, _ in vault}))
                return f"Vault posten: {count} Uploads ({names})"[:140], lambda: run_vault(cfg, vault, open_state)
            todo = campaigns.due(cfg, state, now.timestamp(), VAULT_RETRY_SECONDS)
            if todo:
                count = sum(len(clips) for _, _, clips in todo)
                names = ", ".join(sorted({a.get("label") or a["id"] for a, _, _ in todo}))
                return (f"Kampagnen-Clips posten: {count} Uploads ({names})"[:140],
                        lambda: run_vault(cfg, todo, open_state))
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
            if now.timestamp() - (row["ts"] or 0) > QUEUE_MAX_AGE:
                state.mark_video(row["video_id"], "", "", "skipped", note="Lag zu lange in der Warteschlange")
                continue
            if row["kind"] == "livecut":
                cut = dict(row)
                return (f"Live-Clip rendern: {creator['name']}"[:140],
                        lambda: run_livecut(cfg, creator, cut, open_state))
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
                try:
                    campaigns.discover(cfg, state)
                except Exception:
                    log.exception("Kampagnen-Suche abgebrochen")
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
