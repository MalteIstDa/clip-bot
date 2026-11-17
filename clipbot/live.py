"""Live-Clips: Livestreams der Watchlist mitschneiden, den Chat mitlesen und bei Chat-Peaks die Szene ausschneiden.

Ablauf je Creator, der gerade live ist:
- Recorder: ffmpeg schneidet den Stream in kurze Stuecke (nur die letzten Minuten bleiben liegen).
- Chat: Twitch-Chat (anonym, ohne Schluessel) oder YouTube-Live-Chat. Jede Nachricht zaehlt, Lach- und
  Hype-Woerter zaehlen mehr. Ist der Chat nicht lesbar, entscheidet die Lautstaerke des Streams.
- PeakDetector: rastet der Chat im Vergleich zu den Minuten davor aus, wird die Szene davor ausgeschnitten.
- Die Rohszene landet in der Warteschlange (videos, kind "livecut"); die Automatik rendert daraus einen Clip
  fuer den Vault (run.process_livecut).
"""
from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from clipbot import config, genre, sources

log = logging.getLogger("clipbot")

SEGMENT_SECONDS = 6
KEEP_SECONDS = 360           # so viel Stream bleibt auf der Platte liegen
SYNC_SECONDS = 15            # so oft gleicht der LiveManager die laufenden Mitschnitte mit der Watchlist ab
CHAT_SILENT_SECONDS = 600    # kommt so lange keine Nachricht, wird nach Lautstaerke geclippt
RECORDER_RESTARTS = 6        # so oft wird ein abgebrochener Mitschnitt neu gestartet
CATEGORY_SECONDS = 300      # so oft wird die Kategorie des Streams neu gelesen (Wechsel Just Chatting -> Spiel)
FAILURE_PAUSE = 600          # nach einem gescheiterten Mitschnitt so lange warten, bevor es erneut versucht wird

# Woerter, bei denen der Chat lacht oder ausrastet (klein geschrieben, ganze Woerter; Emojis als Zeichen)
LAUGH = {"kekw", "lul", "lol", "lmao", "lmfao", "omegalul", "icant", "xd", "xdd", "xddd", "haha", "hahaha",
         "hahahaha", "hah", "pepelaugh", "kek", "lachflash", "ptdr", "mdr", "xddd", "jajaja", "hihi", "ahah",
         "ahahah", "lolol", "rofl", "😂", "🤣", "💀", "😭"}
HYPE = {"pog", "pogchamp", "poggers", "pogu", "w", "ww", "www", "omg", "wtf", "holy", "sheesh", "pagman",
        "pogo", "ez", "wow", "krass", "digga", "alter", "letsgo", "lets", "gg", "insane", "crazy", "monka",
        "monkas", "o7", "😱", "🔥", "😳", "‼️", "clip", "clipit", "clipped"}
_TOKEN = re.compile(r"[\w']+|[^\w\s]", re.UNICODE)


def message_weight(text: str) -> float:
    """Wie viel eine Chat-Nachricht zum Peak beitraegt: 1, plus mehr fuer Lachen, Hype und 'clip'."""
    words = [w.lower() for w in _TOKEN.findall(text or "")]
    weight = 1.0
    if any(w in LAUGH or re.fullmatch(r"(ha){2,}h?|(he){3,}|x+d+|l+o+l+|a?(ha)+", w) for w in words):
        weight += 1.0
    if any(w in HYPE for w in words) or "!!!" in (text or "") or "???" in (text or ""):
        weight += 0.5
    if "clip" in (text or "").lower():
        weight += 0.5
    return weight


def is_laugh(text: str) -> bool:
    return message_weight(text) >= 2.0 and any(
        w.lower() in LAUGH or re.fullmatch(r"(ha){2,}h?|x+d+|l+o+l+", w.lower()) for w in _TOKEN.findall(text or ""))


class PeakDetector:
    """Findet Momente, in denen der Chat (oder die Lautstaerke) deutlich ueber dem Normalmass liegt.

    Werte werden je Sekunde gesammelt. Ein Peak liegt vor, wenn die Summe der letzten `window` Sekunden
    mindestens `factor`-mal so gross ist wie ein typisches Fenster der letzten `history` Sekunden
    und ausserdem `min_score` erreicht. Nach einem Peak wird `cooldown` Sekunden lang nichts gemeldet."""

    def __init__(self, window: int = 8, history: int = 300, factor: float = 3.0, min_score: float = 12.0,
                 cooldown: float = 90.0, warmup: float = 60.0):
        self.window, self.history, self.factor = window, history, factor
        self.min_score, self.cooldown, self.warmup = min_score, cooldown, warmup
        self.buckets: dict[int, float] = {}
        self.laughs: dict[int, int] = {}
        self.started: float | None = None
        self.last_peak = -1e18
        self.lock = threading.Lock()  # der Chat schreibt aus einem eigenen Thread

    def add(self, ts: float, value: float = 1.0, laugh: bool = False) -> None:
        sec = int(ts)
        with self.lock:
            if self.started is None:
                self.started = ts
            self.buckets[sec] = self.buckets.get(sec, 0.0) + value
            if laugh:
                self.laughs[sec] = self.laughs.get(sec, 0) + 1

    def _sum(self, end: int, length: int) -> float:
        return sum(self.buckets.get(s, 0.0) for s in range(end - length + 1, end + 1))

    def baseline(self, now: float) -> float:
        """Typisches Fenster der letzten Minuten (Median, damit ein einzelner Peak die Grundlinie nicht hebt)."""
        end = int(now) - self.window
        sums = sorted(self._sum(e, self.window) for e in range(end - self.history + 1, end + 1, max(1, self.window // 2))
                      if self.started is not None and e >= int(self.started))
        return sums[len(sums) // 2] if sums else 0.0

    def check(self, now: float) -> dict | None:
        """Meldet einen Peak als {"ts": Beginn des Anstiegs, "at": now, "score", "base", "laughs"} oder None."""
        with self.lock:
            return self._check(now)

    def _check(self, now: float) -> dict | None:
        if self.started is None or now - self.started < self.warmup or now - self.last_peak < self.cooldown:
            return None
        end = int(now)
        score = self._sum(end, self.window)
        base = self.baseline(now)
        if score < self.min_score or score < self.factor * max(base, 1e-9):
            return None
        # Beginn des Anstiegs: erste Sekunde im Fenster, die deutlich ueber dem Mittel der Grundlinie liegt
        per_sec = base / self.window if base else 0.0
        start = end - self.window + 1
        for sec in range(end - self.window + 1, end + 1):
            if self.buckets.get(sec, 0.0) > 2 * per_sec:
                start = sec
                break
        self.last_peak = now
        laughs = sum(self.laughs.get(s, 0) for s in range(end - self.window + 1, end + 1))
        self._forget(now)
        return {"ts": float(start), "at": now, "score": round(score, 1), "base": round(base, 1), "laughs": laughs}

    def _forget(self, now: float) -> None:
        cut = int(now) - self.history - 4 * self.window
        for table in (self.buckets, self.laughs):
            for sec in [s for s in table if s < cut]:
                del table[sec]


# ---------------------------------------------------------------- Chat
def twitch_login(url: str) -> str | None:
    """Kanalname aus einer Twitch-Adresse (twitch.tv/papaplatte -> papaplatte)."""
    m = re.match(r"https?://(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{2,25})/?(?:$|[?#])", (url or "").strip())
    return m.group(1).lower() if m and m.group(1).lower() not in ("videos", "directory", "settings") else None


def parse_irc(line: str) -> tuple[str, str] | None:
    """Eine Chatzeile aus dem Twitch-IRC: (Absender, Text) fuer PRIVMSG, sonst None."""
    if line.startswith("@"):  # Tags abschneiden
        line = line.split(" ", 1)[1] if " " in line else ""
    m = re.match(r":(\w+)!\S+ PRIVMSG #\w+ :(.*)", line)
    return (m.group(1), m.group(2)) if m else None


class TwitchChat:
    """Liest den Twitch-Chat anonym (ohne Konto und Schluessel) ueber IRC."""
    HOST, PORT = "irc.chat.twitch.tv", 6667

    def __init__(self, login: str, on_message, connect=None):
        self.login, self.on_message = login, on_message
        self.connect = connect or (lambda: socket.create_connection((self.HOST, self.PORT), timeout=30))

    def run(self, stop: threading.Event) -> None:
        delay = 2.0
        while not stop.is_set():
            try:
                self._session(stop)
                delay = 2.0
            except OSError as exc:
                log.debug("Twitch-Chat %s: %s", self.login, exc)
            if stop.wait(delay):
                return
            delay = min(delay * 2, 60.0)

    def _session(self, stop: threading.Event) -> None:
        sock = self.connect()
        try:
            sock.settimeout(1.0)
            nick = f"justinfan{int(time.time()) % 90000 + 10000}"
            sock.sendall(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.login}\r\n".encode())
            buf = b""
            while not stop.is_set():
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    return  # Verbindung beendet, run() verbindet neu
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace")
                    if line.startswith("PING"):
                        sock.sendall(("PONG" + line[4:] + "\r\n").encode())
                        continue
                    msg = parse_irc(line)
                    if msg:
                        self.on_message(time.time(), msg[1])
        finally:
            sock.close()


def youtube_chat(url: str, on_message, stop: threading.Event) -> None:
    """Liest den Live-Chat eines YouTube-Streams (inoffizielle Schnittstelle ueber das Paket chat-downloader)."""
    try:
        from chat_downloader import ChatDownloader
    except ImportError:
        log.info("YouTube-Chat: Paket chat-downloader fehlt, es wird nach Lautstaerke geclippt.")
        return
    delay = 5.0
    while not stop.is_set():
        try:
            chat = ChatDownloader().get_chat(url, message_groups=["messages"], timeout=None, inactivity_timeout=120)
            for item in chat:
                if stop.is_set():
                    chat.close()
                    return
                on_message(time.time(), item.get("message") or "")
            return  # Chat zu Ende = Stream zu Ende
        except Exception as exc:
            log.debug("YouTube-Chat %s: %s", url, exc)
        if stop.wait(delay):
            return
        delay = min(delay * 2, 120.0)


# ---------------------------------------------------------------- Mitschnitt
def stream_url(page_url: str, max_height: int) -> str:
    """Direkte Adresse des Live-Videos (HLS) ueber yt-dlp."""
    import yt_dlp

    fmt = f"best[height<={max_height}]/best"
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "format": fmt}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=False) or {}
    url = info.get("url") or next((f.get("url") for f in reversed(info.get("formats") or []) if f.get("url")), None)
    if not url:
        raise sources.SourceError("Keine Live-Adresse gefunden")
    return url


class Recorder:
    """Schneidet den Stream mit ffmpeg in Stuecke von SEGMENT_SECONDS Sekunden und merkt sich ihre Uhrzeit."""

    def __init__(self, page_url: str, folder: Path, max_height: int = 720, resolve=stream_url):
        self.page_url, self.folder, self.max_height, self.resolve = page_url, Path(folder), max_height, resolve
        self.proc: subprocess.Popen | None = None
        self.runs = 0
        self.known: dict[str, tuple[float, float]] = {}  # Datei -> (Beginn, Ende) als Uhrzeit

    def start(self) -> None:
        self.runs += 1
        run_dir = self.folder / f"teil{self.runs}"
        run_dir.mkdir(parents=True, exist_ok=True)
        url = self.resolve(self.page_url, self.max_height)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", url, "-map", "0:v:0?", "-map", "0:a:0?",
               "-c", "copy", "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-reset_timestamps", "1",
               "-segment_list", str(run_dir / "liste.csv"), "-segment_list_type", "csv",
               str(run_dir / "s%06d.ts")]
        self.started = time.time()
        self.run_dir = run_dir
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def refresh(self) -> None:
        """Fertige Stuecke aus der Liste von ffmpeg uebernehmen (Zeiten relativ zum Start -> Uhrzeit)."""
        listing = getattr(self, "run_dir", None)
        if not listing or not (listing / "liste.csv").exists():
            return
        rows = list(csv.reader((listing / "liste.csv").read_text(encoding="utf-8", errors="replace").splitlines()))
        rows = [r for r in rows if len(r) >= 3]
        if not rows:
            return
        first = float(rows[0][1])
        for name, a, b in (r[:3] for r in rows):
            path = str(listing / name)
            if path not in self.known:
                self.known[path] = (self.started + float(a) - first, self.started + float(b) - first)

    def segments(self) -> list[tuple[Path, float, float]]:
        self.refresh()
        return sorted(((Path(p), a, b) for p, (a, b) in self.known.items() if Path(p).exists()), key=lambda x: x[1])

    def covered_until(self) -> float:
        segs = self.segments()
        return segs[-1][2] if segs else 0.0

    def prune(self, now: float, keep: float = KEEP_SECONDS) -> None:
        for path, _, end in self.segments():
            if end < now - keep:
                path.unlink(missing_ok=True)
                self.known.pop(str(path), None)

    def cut(self, start: float, end: float, out: Path) -> tuple[Path, float, float] | None:
        """Die Stuecke von start bis end (Uhrzeit) zu einer Datei verbinden.
        Gibt (Datei, Uhrzeit der ersten Sekunde der Datei, Laenge in Sekunden) zurueck."""
        parts = [(p, a, b) for p, a, b in self.segments() if b > start and a < end]
        if not parts:
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        listing = out.with_suffix(".txt")
        listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p, _, _ in parts), encoding="utf-8")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
               "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(out)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        listing.unlink(missing_ok=True)
        if result.returncode != 0 or not out.exists():
            cmd = [c for c in cmd if c not in ("-bsf:a", "aac_adtstoasc")]  # Stream ohne AAC-Ton
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                log.warning("Live-Szene konnte nicht ausgeschnitten werden: %s", result.stderr[-300:])
                return None
        return out, parts[0][1], parts[-1][2] - parts[0][1]


def loudness_db(path: Path) -> list[float]:
    """Lautstaerke je Sekunde eines Stuecks (fuer Streams ohne lesbaren Chat)."""
    from clipbot import highlights
    try:
        return [float(v) for v in highlights.loudness_per_second(path)]
    except Exception:
        return []


# ---------------------------------------------------------------- Ein Stream
class Session(threading.Thread):
    """Beobachtet einen laufenden Stream: Mitschnitt + Chat + Peaks -> Rohszenen in der Warteschlange."""

    def __init__(self, creator: dict, page_url: str, title: str, settings: dict, open_state,
                 recorder: Recorder | None = None, chat=None, clock=time.time):
        super().__init__(daemon=True, name=f"live-{creator['id']}")
        self.creator, self.page_url, self.title, self.s = creator, page_url, title or "", settings
        self.open_state, self.clock = open_state, clock
        self.platform = sources.platform_of(creator["url"])
        self.stream_id = f"live-{creator['id']}-{time.strftime('%Y%m%d-%H%M', time.localtime(clock()))}"
        self.folder = Path(settings["work_dir"]) / "live" / self.stream_id
        self.recorder = recorder or Recorder(page_url, self.folder / "mitschnitt", min(720, settings["max_height"]))
        self.chat = chat
        self.stop_event = threading.Event()
        factor = {"niedrig": 4.0, "normal": 3.0, "hoch": 2.2}.get(str(settings["live_sensitivity"]), 3.0)
        self.detector = PeakDetector(factor=factor, cooldown=float(settings["live_cooldown_seconds"]))
        self.audio = PeakDetector(factor=2.2, min_score=0.0, cooldown=float(settings["live_cooldown_seconds"]),
                                  warmup=120.0)
        self.pending: deque[dict] = deque()
        self.messages = 0
        self.last_message = 0.0
        self.started_at = clock()
        self.cuts = 0
        self.mode = "chat" if self._chat_source() else "laut"
        self.error = ""
        self.restarts, self.last_start = 0, 0.0
        self.category: str | None = None
        self.last_category = -1e18
        self._heard: set[str] = set()

    # -- Chat
    def _chat_source(self):
        if self.chat is not None:
            return self.chat
        if self.platform == "twitch" and twitch_login(self.creator["url"]):
            reader = TwitchChat(twitch_login(self.creator["url"]), self.on_message)
            self.chat = reader.run
        elif self.platform == "youtube":
            self.chat = lambda stop: youtube_chat(self.page_url, self.on_message, stop)
        return self.chat

    def on_message(self, ts: float, text: str) -> None:
        self.messages += 1
        self.last_message = ts
        self.detector.add(ts, message_weight(text), is_laugh(text))

    # -- Status fuers Dashboard
    def status(self) -> dict:
        now = self.clock()
        minutes = max(1.0, (now - self.started_at) / 60)
        kind = genre.classify(self.category, self.title)
        return {"creator": self.creator["id"], "name": self.creator.get("name") or self.creator["id"],
                "title": self.title, "since": self.started_at, "mode": self.mode,
                "category": self.category or "", "genre": genre.describe(kind, self.category),
                "per_minute": round(self.messages / minutes), "cuts": self.cuts, "recording": self.recorder.alive(),
                "error": self.error}

    def stop(self) -> None:
        self.stop_event.set()

    # -- Ablauf
    def run(self) -> None:
        log.info("[live] %s ist live: Mitschnitt und Chat laufen.", self.creator.get("name"))
        if self.chat:
            threading.Thread(target=self.chat, args=(self.stop_event,), daemon=True).start()
        self.last_start = self.clock()
        try:
            self.recorder.start()
        except Exception as exc:
            self.error = f"Mitschnitt startet nicht: {str(exc)[:200]}"
            log.warning("[live] %s: %s", self.creator.get("name"), self.error)
            self.stop_event.set()
        try:
            while not self.stop_event.wait(1.0):
                self.step(self.clock())
        finally:
            self.recorder.stop()
            # restliche Szenen noch ausschneiden, soweit der Mitschnitt reicht
            for peak in list(self.pending):
                self._cut(peak)
            self.pending.clear()
            shutil.rmtree(self.folder / "mitschnitt", ignore_errors=True)
            log.info("[live] %s: Beobachtung beendet, %d Szenen gesammelt.", self.creator.get("name"), self.cuts)

    def lookup_category(self) -> str | None:
        """Kategorie des Streams: Twitch ueber die Webseiten-Schnittstelle, YouTube ueber yt-dlp."""
        if self.platform == "twitch" and twitch_login(self.creator["url"]):
            return genre.twitch_category(twitch_login(self.creator["url"]))
        if self.platform == "youtube":
            cats = sources.probe(self.page_url).get("categories") or []
            return cats[0] if cats else None
        return None

    def _update_category(self) -> None:
        try:
            found = self.lookup_category()
        except Exception as exc:
            log.debug("[live] Kategorie von %s: %s", self.creator.get("name"), exc)
            return
        if found and found != self.category:
            log.info("[live] %s: Kategorie %s", self.creator.get("name"), found)
            self.category = found

    def step(self, now: float) -> None:
        """Ein Takt: Mitschnitt pruefen, Peaks suchen, faellige Szenen ausschneiden."""
        if now - self.last_category >= CATEGORY_SECONDS:
            self.last_category = now
            threading.Thread(target=self._update_category, daemon=True).start()
        if not self.recorder.alive() and not self.stop_event.is_set() and now - self.last_start >= 20:
            # Abbruch (z. B. abgelaufene Stream-Adresse): neu starten, aber nicht endlos
            self.restarts = 0 if now - self.last_start > 600 else self.restarts + 1
            if self.restarts >= RECORDER_RESTARTS:
                self.error = "Der Mitschnitt bricht immer wieder ab."
                self.stop_event.set()
                return
            self.last_start = now
            try:
                self.recorder.start()
            except Exception as exc:
                self.error = f"Mitschnitt startet nicht: {str(exc)[:200]}"
        if self.mode == "chat" and now - max(self.last_message, self.started_at) > CHAT_SILENT_SECONDS:
            self.mode = "laut"  # Chat stumm oder nicht lesbar: nach Lautstaerke clippen
            log.info("[live] %s: Kein Chat lesbar, es wird nach Lautstaerke geclippt.", self.creator.get("name"))
        elif self.mode == "laut" and self.messages and now - self.last_message < 30:
            self.mode = "chat"
        if self.mode == "laut":
            self._listen()
        peak = (self.detector if self.mode == "chat" else self.audio).check(now)
        if peak and self.cuts + len(self.pending) < int(self.s["live_max_clips_per_stream"]):
            log.info("[live] %s: Chat-Peak (%.0f statt %.0f)%s", self.creator.get("name"), peak["score"],
                     peak["base"], f", {peak['laughs']}x Lachen" if peak.get("laughs") else "")
            self.pending.append(peak)
        before, after = float(self.s["live_before_seconds"]), float(self.s["live_after_seconds"])
        covered = self.recorder.covered_until()
        while self.pending and covered >= self.pending[0]["ts"] + after + 10:
            self._cut(self.pending.popleft())
        while self.pending and now - self.pending[0]["at"] > before + after + 180:
            self.pending.popleft()  # der Mitschnitt hat die Stelle nie erreicht
        self.recorder.prune(now, max(KEEP_SECONDS, before + after + 240))

    def _listen(self) -> None:
        """Ohne Chat: Lautstaerke der neuen Stuecke als Signal (laute Momente = Lachen, Schreien, Action)."""
        for path, a, _ in self.recorder.segments():
            if str(path) in self._heard:
                continue
            self._heard.add(str(path))
            for i, db in enumerate(loudness_db(path)):
                self.audio.add(a + i, 10 ** (db / 20) * 100)

    def _cut(self, peak: dict) -> None:
        before, after = float(self.s["live_before_seconds"]), float(self.s["live_after_seconds"])
        start, end = peak["ts"] - before, peak["ts"] + after
        cut_id = f"{self.stream_id}-{int(peak['ts']) % 100000:05d}"
        result = self.recorder.cut(start - 10, end + 10, self.folder / "szenen" / f"{cut_id}.mp4")
        if not result:
            return
        path, first, length = result
        clip_start = max(0.0, start - first)
        note = {"start": clip_start, "end": min(length, clip_start + before + after), "duration": length,
                "score": peak["score"], "base": peak["base"], "laughs": peak.get("laughs", 0),
                "mode": self.mode, "stream_title": self.title, "at": peak["ts"], "category": self.category or ""}
        state = self.open_state()
        try:
            state.mark_video(cut_id, "", self.title or self.creator.get("name", ""), "queued",
                             creator=self.creator["id"], url=str(path), kind="livecut",
                             note=json.dumps(note, ensure_ascii=False))
            state.set_meta(f"live_session:{self.creator['id']}", str(self.clock()))
        finally:
            state.close()
        self.cuts += 1
        log.info("[live] %s: Szene %d gesammelt, sie wird gleich als Clip gerendert.", self.creator.get("name"),
                 self.cuts)


# ---------------------------------------------------------------- Alle Streams
def wanted_streams(cfg: dict, watched: dict[str, dict]) -> dict[str, tuple[dict, str, str]]:
    """Welche Creator jetzt mitgeschnitten werden sollen: {id: (creator, Stream-Adresse, Titel)}."""
    from clipbot import watch

    s = cfg["settings"]
    if not (s["autopilot"] and s["live_clips"]):
        return {}
    out = {}
    for creator in cfg.get("creators") or []:
        w = watched.get(creator["id"]) or {}
        if not w.get("live") or not watch.wants_clips(cfg, creator):
            continue
        platform = sources.platform_of(creator["url"])
        if platform not in ("twitch", "youtube", "kick"):
            continue
        url = w.get("live_url") or creator["url"]
        out[creator["id"]] = (creator, url, w.get("live_title") or "")
        if len(out) >= int(s["live_max_streams"]):
            break
    return out


class LiveManager:
    """Startet und beendet die Mitschnitte passend zur Watchlist (wer ist live?)."""

    def __init__(self, open_state, session_factory=Session):
        self.open_state, self.session_factory = open_state, session_factory
        self.sessions: dict[str, Session] = {}
        self.failures: dict[str, float] = {}
        self.lock = threading.Lock()

    def sync(self, cfg: dict) -> None:
        s = cfg["settings"]
        state = self.open_state(s)
        try:
            watched = state.watch_all()
        finally:
            state.close()
        wanted = wanted_streams(cfg, watched)
        now = time.time()
        with self.lock:
            for cid, session in list(self.sessions.items()):
                if not session.is_alive():
                    if session.error:
                        self.failures[cid] = now  # nicht sofort wieder versuchen
                    self.sessions.pop(cid, None)
                elif cid not in wanted:
                    session.stop()  # Stream vorbei oder Creator abgeschaltet; der Thread raeumt selbst auf
                    self.sessions.pop(cid, None)
            for cid, (creator, url, title) in wanted.items():
                if cid in self.sessions or now - self.failures.get(cid, 0) < FAILURE_PAUSE:
                    continue
                session = self.session_factory(creator, url, title, s, lambda: self.open_state(s))
                self.sessions[cid] = session
                session.start()

    def stop_all(self) -> None:
        with self.lock:
            for session in self.sessions.values():
                session.stop()
            self.sessions.clear()

    def status(self) -> list[dict]:
        with self.lock:
            return [s.status() for s in self.sessions.values()]

    def loop(self) -> None:
        time.sleep(6)
        while True:
            try:
                self.sync(config.load(allow_empty=True))
            except config.ConfigError:
                pass
            except Exception:
                log.exception("Live: Abgleich abgebrochen")
            time.sleep(SYNC_SECONDS)

    def start(self) -> None:
        threading.Thread(target=self.loop, daemon=True, name="live-manager").start()
