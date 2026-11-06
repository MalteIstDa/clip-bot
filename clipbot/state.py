"""Kleine SQLite-Datenbank: merkt sich verarbeitete Videos, Clips und Uploads."""
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
"""


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(clips)")}
        for col in ("texts", "hook", "reason"):  # neuere Spalten in alten Datenbanken nachruesten
            if col not in cols:
                self.db.execute(f"ALTER TABLE clips ADD COLUMN {col} TEXT")
        self.db.commit()

    def video_done(self, video_id: str) -> bool:
        row = self.db.execute("SELECT status FROM videos WHERE video_id=?", (video_id,)).fetchone()
        return bool(row) and row["status"] in ("done", "skipped")

    def mark_video(self, video_id: str, channel: str, title: str, status: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?)",
            (video_id, channel, title, status, time.time()),
        )
        self.db.commit()

    def add_clip(self, clip_id, video_id, channel, path, title, caption, start, end,
                 texts: dict | None = None, hook: str = "", reason: str = "") -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO clips (clip_id, video_id, channel, path, title, caption, start, end, ts,
               texts, hook, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (clip_id, video_id, channel, str(path), title, caption, start, end, time.time(),
             json.dumps(texts, ensure_ascii=False) if texts else None, hook, reason),
        )
        self.db.commit()

    @staticmethod
    def texts_for(clip, platform: str) -> tuple[str, str]:
        """Titel und Caption fuer eine Plattform: KI-Fassung, falls vorhanden, sonst die allgemeine."""
        try:
            own = (json.loads(clip["texts"] or "{}") or {}).get(platform) or {}
        except (IndexError, KeyError, ValueError):
            own = {}
        return own.get("title") or clip["title"], own.get("caption") or clip["caption"]

    def pending_clips(self, channel: str, platform: str) -> list[sqlite3.Row]:
        """Clips, die auf dieser Plattform noch nicht erfolgreich hochgeladen wurden (aelteste zuerst)."""
        return self.db.execute(
            """SELECT c.* FROM clips c
               LEFT JOIN uploads u ON u.clip_id=c.clip_id AND u.platform=? AND u.status='ok'
               WHERE c.channel=? AND u.clip_id IS NULL ORDER BY c.ts""",
            (platform, channel),
        ).fetchall()

    def mark_upload(self, clip_id, platform, status, remote_id=None, error=None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO uploads VALUES (?,?,?,?,?,?)",
            (clip_id, platform, status, remote_id, error, time.time()),
        )
        self.db.commit()

    # ---- fuer das Dashboard ----
    def clips(self) -> list[dict]:
        uploads: dict[str, dict] = {}
        for u in self.db.execute("SELECT * FROM uploads"):
            uploads.setdefault(u["clip_id"], {})[u["platform"]] = {
                "status": u["status"], "remote_id": u["remote_id"], "error": u["error"], "ts": u["ts"]}
        rows = self.db.execute("SELECT * FROM clips ORDER BY ts DESC").fetchall()
        return [{**dict(r), "uploads": uploads.get(r["clip_id"], {})} for r in rows]

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
        self.db.execute("DELETE FROM clips WHERE clip_id=?", (clip_id,))
        self.db.commit()

    def videos(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT * FROM videos ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.db.close()
