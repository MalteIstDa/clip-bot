"""Kleine SQLite-Datenbank: Videos, Clips, Uploads je Konto, Best-of-Momente, Watchlist-Stand, Benachrichtigungen."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY, channel TEXT, title TEXT, status TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS clips (
    clip_id TEXT PRIMARY KEY, video_id TEXT, channel TEXT, path TEXT,
    title TEXT, caption TEXT, start REAL, end REAL, ts REAL
);
CREATE TABLE IF NOT EXISTS uploads (
    clip_id TEXT, platform TEXT, status TEXT, remote_id TEXT, error TEXT, ts REAL,
    PRIMARY KEY (clip_id, platform)
);
CREATE TABLE IF NOT EXISTS posts (
    clip_id TEXT, account TEXT, platform TEXT, status TEXT, remote_id TEXT, error TEXT, ts REAL,
    PRIMARY KEY (clip_id, account)
);
CREATE TABLE IF NOT EXISTS moments (
    moment_id TEXT PRIMARY KEY, video_id TEXT, creator TEXT, creator_name TEXT, video_title TEXT, path TEXT,
    duration REAL, score REAL, text TEXT, words TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS moment_use (
    moment_id TEXT, channel TEXT, clip_id TEXT, PRIMARY KEY (moment_id, channel)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, title TEXT, text TEXT, url TEXT, creator TEXT,
    read INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS watch (
    creator TEXT PRIMARY KEY, channel_id TEXT, baseline INTEGER DEFAULT 0, live INTEGER DEFAULT 0,
    live_title TEXT, live_url TEXT, live_since REAL, last_title TEXT, last_url TEXT, last_ts REAL,
    last_check REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

REVIEW_STATES = ("wartet", "freigegeben", "verworfen", "vault")  # vault = Live-Clip, wartet im Vault aufs Posten
# Videos: seen = bekannt, nichts zu tun | upcoming/live/processing = Stream angekuendigt, laeuft, wird noch verarbeitet
# queued = wartet auf das automatische Clippen | done | skipped | failed
WAITING = ("upcoming", "live", "processing")
_WATCH_FIELDS = ("channel_id", "baseline", "live", "live_title", "live_url", "live_since", "last_title",
                 "last_url", "last_ts", "last_check", "error")


class State:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._add_columns("clips", ("texts", "hook", "reason", "review", "transcript", "creator", "kind", "genre"))
        self._add_columns("videos", ("creator", "url", "kind", "note"))
        self._add_columns("videos", ("seen",), "REAL")
        self._migrate_uploads()
        self.db.commit()

    def _add_columns(self, table: str, columns, sql_type: str = "TEXT") -> None:
        """Neuere Spalten in alten Datenbanken nachruesten."""
        have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
        for col in columns:
            if col not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}")

    def _migrate_uploads(self) -> None:
        """Frueher gab es je Kanal und Plattform genau ein Konto. Diese Uploads den neuen Konten zuordnen
        (Konto-Kurzname = <kanal>_<plattform>, so legt config.py die alten Konten an)."""
        if self.get_meta("uploads_migrated"):
            return
        self.db.execute(
            """INSERT OR IGNORE INTO posts (clip_id, account, platform, status, remote_id, error, ts)
               SELECT u.clip_id, c.channel || '_' || u.platform, u.platform, u.status, u.remote_id, u.error, u.ts
               FROM uploads u JOIN clips c ON c.clip_id = u.clip_id""")
        self.set_meta("uploads_migrated", "1")

    # ---- Merker ----
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))
        self.db.commit()

    # ---- Videos ----
    def video_status(self, video_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM videos WHERE video_id=?", (video_id,)).fetchone()
        return row["status"] if row else None

    def video_done(self, video_id: str) -> bool:
        return self.video_status(video_id) in ("done", "skipped")

    def mark_video(self, video_id: str, channel: str, title: str, status: str, creator: str = "",
                   url: str = "", kind: str = "", note: str = "") -> None:
        """Legt das Video an oder aktualisiert es. Leere Angaben ueberschreiben vorhandene nicht."""
        now = time.time()
        self.db.execute(
            """INSERT INTO videos (video_id, channel, title, status, ts, creator, url, kind, note, seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET status=excluded.status, ts=excluded.ts, note=excluded.note,
                 channel=COALESCE(NULLIF(excluded.channel, ''), channel),
                 title=COALESCE(NULLIF(excluded.title, ''), title),
                 creator=COALESCE(NULLIF(excluded.creator, ''), creator),
                 url=COALESCE(NULLIF(excluded.url, ''), url),
                 kind=COALESCE(NULLIF(excluded.kind, ''), kind)""",
            (video_id, channel, title, status, now, creator, url, kind, note, now),
        )
        self.db.commit()

    def get_video(self, video_id: str):
        return self.db.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()

    def videos_by_status(self, statuses, creator: str | None = None) -> list[sqlite3.Row]:
        """Videos mit einem der Zustaende, aelteste zuerst."""
        marks = ",".join("?" * len(statuses))
        sql, args = f"SELECT * FROM videos WHERE status IN ({marks})", list(statuses)
        if creator is not None:
            sql, args = sql + " AND creator=?", args + [creator]
        return self.db.execute(sql + " ORDER BY ts", args).fetchall()

    def videos(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT * FROM videos ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---- Clips ----
    def add_clip(self, clip_id, video_id, channel, path, title, caption, start, end,
                 texts: dict | None = None, hook: str = "", reason: str = "",
                 review: str = "wartet", transcript: str = "", creator: str = "", kind: str = "clip",
                 genre: str = "") -> None:
        """review: wartet (auf Freigabe im Dashboard) | freigegeben | verworfen. kind: clip | bestof | explainer."""
        self.db.execute(
            """INSERT OR REPLACE INTO clips (clip_id, video_id, channel, path, title, caption, start, end, ts,
               texts, hook, reason, review, transcript, creator, kind, genre)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (clip_id, video_id, channel, str(path), title, caption, start, end, time.time(),
             json.dumps(texts, ensure_ascii=False) if texts else None, hook, reason, review, transcript,
             creator, kind, genre),
        )
        self.db.commit()

    def set_review(self, clip_id: str, review: str) -> None:
        if review not in REVIEW_STATES:
            raise ValueError(review)
        self.db.execute("UPDATE clips SET review=? WHERE clip_id=?", (review, clip_id))
        self.db.commit()

    def set_texts(self, clip_id: str, title: str, caption: str, platforms: dict | None) -> None:
        """Texte komplett ersetzen (Freigabe-Ansicht, neu texten)."""
        self.db.execute("UPDATE clips SET title=?, caption=?, texts=? WHERE clip_id=?",
                        (title, caption, json.dumps(platforms, ensure_ascii=False) if platforms else None, clip_id))
        self.db.commit()

    @staticmethod
    def texts_for(clip, platform: str) -> tuple[str, str]:
        """Titel und Caption fuer eine Plattform: KI-Fassung, falls vorhanden, sonst die allgemeine."""
        try:
            own = (json.loads(clip["texts"] or "{}") or {}).get(platform) or {}
        except (IndexError, KeyError, ValueError):
            own = {}
        return own.get("title") or clip["title"], own.get("caption") or clip["caption"]

    def pending_clips(self, channel: str, account: str, platform: str | None = None,
                      exclusive: bool = False) -> list[sqlite3.Row]:
        """Freigegebene Clips des Kanals, die dieses Konto noch nicht hochgeladen hat (aelteste zuerst).
        exclusive: Clips auslassen, die schon ein anderes Konto derselben Plattform hochgeladen hat
        (Verteilung 'abwechselnd')."""
        sql = """SELECT c.* FROM clips c
                 LEFT JOIN posts p ON p.clip_id=c.clip_id AND p.account=? AND p.status='ok'
                 WHERE c.channel=? AND c.review='freigegeben' AND p.clip_id IS NULL"""
        args = [account, channel]
        if exclusive and platform:
            sql += """ AND NOT EXISTS (SELECT 1 FROM posts o WHERE o.clip_id=c.clip_id AND o.platform=?
                       AND o.status='ok' AND o.account<>?)"""
            args += [platform, account]
        return self.db.execute(sql + " ORDER BY c.ts", args).fetchall()

    # ---- Vault (Live-Clips) ----
    def vault_counts(self) -> dict[str, int]:
        """Zahl der Clips im Vault je Kanal."""
        rows = self.db.execute("SELECT channel, COUNT(*) AS n FROM clips WHERE review='vault' GROUP BY channel")
        return {r["channel"]: r["n"] for r in rows}

    def release_vault(self, channel: str) -> int:
        """Alle Clips des Kanals aus dem Vault freigeben. Gibt ihre Zahl zurueck."""
        cur = self.db.execute("UPDATE clips SET review='freigegeben' WHERE channel=? AND review='vault'", (channel,))
        self.db.commit()
        return cur.rowcount

    def failed_posts_since(self, account: str, since: float) -> int:
        row = self.db.execute("SELECT COUNT(*) AS n FROM posts WHERE account=? AND status='failed' AND ts>=?",
                              (account, since)).fetchone()
        return row["n"]

    def mark_post(self, clip_id: str, account: str, platform: str, status: str, remote_id=None, error=None) -> None:
        self.db.execute("INSERT OR REPLACE INTO posts VALUES (?,?,?,?,?,?,?)",
                        (clip_id, account, platform, status, remote_id, error, time.time()))
        self.db.commit()

    def posts_since(self, account: str, since: float) -> int:
        """Zahl der Upload-Versuche dieses Kontos seit dem Zeitpunkt (fuer den Zeitplan)."""
        row = self.db.execute("SELECT COUNT(*) AS n FROM posts WHERE account=? AND ts>=?", (account, since)).fetchone()
        return row["n"]

    def last_post(self, account: str):
        return self.db.execute("SELECT * FROM posts WHERE account=? AND status='ok' ORDER BY ts DESC LIMIT 1",
                               (account,)).fetchone()

    # ---- fuer das Dashboard ----
    def clips(self) -> list[dict]:
        uploads: dict[str, dict] = {}
        for u in self.db.execute("SELECT * FROM posts"):
            uploads.setdefault(u["clip_id"], {})[u["account"]] = {
                "platform": u["platform"], "status": u["status"], "remote_id": u["remote_id"],
                "error": u["error"], "ts": u["ts"]}
        rows = self.db.execute("SELECT * FROM clips ORDER BY ts DESC").fetchall()
        out = []
        for r in rows:
            clip = {**dict(r), "uploads": uploads.get(r["clip_id"], {})}
            clip["review"] = clip.get("review") or "wartet"
            clip["kind"] = clip.get("kind") or "clip"
            try:
                clip["texts"] = json.loads(clip.get("texts") or "{}") or {}
            except ValueError:
                clip["texts"] = {}
            clip.pop("transcript", None)
            out.append(clip)
        return out

    def get_clip(self, clip_id: str):
        return self.db.execute("SELECT * FROM clips WHERE clip_id=?", (clip_id,)).fetchone()

    def update_clip(self, clip_id: str, title: str, caption: str) -> None:
        # Eigene Aenderungen im Dashboard gelten fuer alle Plattformen (KI-Fassungen je Plattform verwerfen).
        self.db.execute(
            """UPDATE clips SET texts=CASE WHEN title=? AND caption=? THEN texts ELSE NULL END,
               title=?, caption=? WHERE clip_id=?""", (title, caption, title, caption, clip_id))
        self.db.commit()

    def delete_clip(self, clip_id: str) -> None:
        self.db.execute("DELETE FROM uploads WHERE clip_id=?", (clip_id,))
        self.db.execute("DELETE FROM posts WHERE clip_id=?", (clip_id,))
        self.db.execute("DELETE FROM moment_use WHERE clip_id=?", (clip_id,))  # Momente wieder freigeben
        self.db.execute("DELETE FROM clips WHERE clip_id=?", (clip_id,))
        self.db.commit()

    # ---- Best-of-Momente ----
    def add_moment(self, moment_id, video_id, creator, creator_name, video_title, path, duration, score,
                   text, words) -> None:
        """words: [[start, ende, wort], ...] mit Zeiten relativ zum Anfang der Moment-Datei."""
        self.db.execute("INSERT OR REPLACE INTO moments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (moment_id, video_id, creator, creator_name, video_title, str(path), duration, score,
                         text, json.dumps(words, ensure_ascii=False), time.time()))
        self.db.commit()

    def unused_moments(self, channel: str, creators, since: float) -> list[dict]:
        """Momente der Creator seit dem Zeitpunkt, die dieser Kanal noch in keinem Best-of gezeigt hat."""
        creators = list(creators)
        if not creators:
            return []
        marks = ",".join("?" * len(creators))
        rows = self.db.execute(
            f"""SELECT m.* FROM moments m LEFT JOIN moment_use u ON u.moment_id=m.moment_id AND u.channel=?
                WHERE u.moment_id IS NULL AND m.ts>=? AND m.creator IN ({marks}) ORDER BY m.score DESC""",
            [channel, since] + creators).fetchall()
        out = []
        for r in rows:
            moment = dict(r)
            try:
                moment["words"] = json.loads(moment["words"] or "[]")
            except ValueError:
                moment["words"] = []
            out.append(moment)
        return out

    def use_moments(self, moment_ids, channel: str, clip_id: str) -> None:
        self.db.executemany("INSERT OR REPLACE INTO moment_use VALUES (?,?,?)",
                            [(m, channel, clip_id) for m in moment_ids])
        self.db.commit()

    def old_moments(self, before: float) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM moments WHERE ts<?", (before,)).fetchall()

    def delete_moment(self, moment_id: str) -> None:
        self.db.execute("DELETE FROM moments WHERE moment_id=?", (moment_id,))
        self.db.execute("DELETE FROM moment_use WHERE moment_id=?", (moment_id,))
        self.db.commit()

    def moment_counts(self, since: float) -> dict[str, int]:
        rows = self.db.execute("SELECT creator, COUNT(*) AS n FROM moments WHERE ts>=? GROUP BY creator", (since,))
        return {r["creator"]: r["n"] for r in rows}

    # ---- Watchlist ----
    def watch_get(self, creator: str) -> dict:
        row = self.db.execute("SELECT * FROM watch WHERE creator=?", (creator,)).fetchone()
        return dict(row) if row else {}

    def watch_set(self, creator: str, **fields) -> None:
        unknown = set(fields) - set(_WATCH_FIELDS)
        if unknown:
            raise ValueError(f"unbekannte Felder: {unknown}")
        self.db.execute("INSERT OR IGNORE INTO watch (creator) VALUES (?)", (creator,))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            self.db.execute(f"UPDATE watch SET {sets} WHERE creator=?", [*fields.values(), creator])
        self.db.commit()

    def watch_all(self) -> dict[str, dict]:
        return {r["creator"]: dict(r) for r in self.db.execute("SELECT * FROM watch")}

    def watch_delete(self, creator: str) -> None:
        self.db.execute("DELETE FROM watch WHERE creator=?", (creator,))
        self.db.commit()

    # ---- Benachrichtigungen ----
    def add_notification(self, kind: str, title: str, text: str = "", url: str = "", creator: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO notifications (ts, kind, title, text, url, creator) VALUES (?,?,?,?,?,?)",
            (time.time(), kind, title, text, url, creator))
        self.db.execute("DELETE FROM notifications WHERE id <= ?", (cur.lastrowid - 300,))  # nur die letzten 300
        self.db.commit()
        return cur.lastrowid

    def notifications(self, limit: int = 40) -> list[dict]:
        rows = self.db.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def unread_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) AS n FROM notifications WHERE read=0").fetchone()["n"]

    def mark_read(self) -> None:
        self.db.execute("UPDATE notifications SET read=1 WHERE read=0")
        self.db.commit()

    def close(self) -> None:
        self.db.close()
