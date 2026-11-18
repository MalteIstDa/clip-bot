"""Live-Clips aus Chat-Peaks und der Vault. Ohne Netz: Chat, Stream und Uploads sind Attrappen, ffmpeg ist echt."""
import json
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

import run
from clipbot import auto, config, live, notify, watch
from clipbot.state import State
from clipbot.transcribe import Word


def s(**kw):
    return {**config.DEFAULTS, **kw}


def channel(**kw):
    return {"name": "clips", "kind": "clips", "language": "de", "creators": [], "accounts": [], "tags": [],
            "credit": "", "title_template": "{hook} #shorts", "caption_template": "{hook}", "clips": True,
            "bestof": "off", "distribute": "alle", **kw}


def fake_words(path, a, b, *rest):
    a = max(0.0, a)
    return [Word(a + i * 0.4, a + i * 0.4 + 0.3, f"w{i}." if i % 6 == 5 else f"w{i}") for i in range(int((b - a) / 0.4))]


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(notify, "_push", lambda *a: None)
    monkeypatch.setattr(live.genre, "twitch_category", lambda login: None)  # kein Netz
    monkeypatch.setattr(live.sources, "probe", lambda url: {"categories": []})
    monkeypatch.setattr(run, "UPLOAD_PAUSE", 0)


# ---------------------------------------------------------------- Chat lesen
def test_laughing_and_hype_messages_weigh_more():
    assert live.message_weight("hallo zusammen") == 1.0
    assert live.message_weight("KEKW") == 2.0 and live.message_weight("hahahaha") == 2.0
    assert live.message_weight("😂😂😂") == 2.0 and live.is_laugh("xDDD")
    assert live.message_weight("W") == 1.5 and live.message_weight("CLIP IT!!!") == 2.0
    assert not live.is_laugh("Pog")


def test_twitch_names_and_chat_lines():
    assert live.twitch_login("https://www.twitch.tv/Papaplatte") == "papaplatte"
    assert live.twitch_login("https://twitch.tv/papaplatte/videos") is None
    assert live.twitch_login("https://www.youtube.com/@Gtasty") is None
    line = "@badge-info=;color=#FF0000 :anna!anna@anna.tmi.twitch.tv PRIVMSG #papaplatte :KEKW was war das"
    assert live.parse_irc(line) == ("anna", "KEKW was war das")
    assert live.parse_irc(":tmi.twitch.tv 001 justinfan123 :Welcome") is None


class FakeSocket:
    def __init__(self, chunks):
        self.chunks, self.sent, self.closed = list(chunks), [], False

    def settimeout(self, t):
        pass

    def sendall(self, data):
        self.sent.append(data.decode())

    def recv(self, n):
        if self.chunks:
            return self.chunks.pop(0)
        return b""

    def close(self):
        self.closed = True


def test_twitch_chat_reads_anonymously_and_answers_ping():
    import threading
    sock = FakeSocket([b":tmi.twitch.tv 001 justinfan1 :Welcome\r\nPING :tmi.twitch.tv\r\n",
                       b":a!a@a PRIVMSG #papaplatte :LUL\r\n:b!b@b PRIVMSG #papa",
                       b"platte :W stream\r\n"])
    got, stop = [], threading.Event()
    chat = live.TwitchChat("papaplatte", lambda ts, text: got.append(text) or (stop.set() if len(got) == 2 else None),
                           connect=lambda: sock)
    chat._session(stop)
    assert got == ["LUL", "W stream"] and sock.closed
    assert sock.sent[0].startswith("PASS SCHMOOPIIE\r\nNICK justinfan") and "JOIN #papaplatte" in sock.sent[0]
    assert "PONG :tmi.twitch.tv\r\n" in sock.sent


# ---------------------------------------------------------------- Peaks
def steady_chat(detector, start, seconds, per_second=1):
    for t in range(start, start + seconds):
        for _ in range(per_second):
            detector.add(t + 0.5, 1.0)


def test_peak_when_chat_explodes():
    d = live.PeakDetector()
    steady_chat(d, 1000, 300)
    assert all(d.check(t) is None for t in range(1100, 1300, 5))  # normales Chatten ist kein Peak
    for t in range(1300, 1306):  # der Chat rastet aus: 8 Nachrichten pro Sekunde, viele lachen
        for _ in range(8):
            d.add(t + 0.2, live.message_weight("KEKW"), True)
    peak = d.check(1306)
    assert peak and peak["ts"] == 1300 and peak["laughs"] == 48 and peak["score"] > 3 * peak["base"]
    for t in range(1306, 1330):  # direkt danach noch laut, aber Pause zwischen zwei Clips
        for _ in range(8):
            d.add(t + 0.2, 2.0)
        assert d.check(t + 1) is None


def test_no_peak_in_quiet_chat_or_right_after_start():
    d = live.PeakDetector()
    for t in range(0, 30):
        d.add(t, 1.0)
    for _ in range(40):
        d.add(31, 2.0)
    assert d.check(32) is None  # noch keine Grundlinie
    quiet = live.PeakDetector()
    steady_chat(quiet, 0, 200)
    quiet.add(201, 2.0)
    quiet.add(201, 2.0)
    assert quiet.check(202) is None  # zu wenig Nachrichten fuer einen Peak


# ---------------------------------------------------------------- Mitschnitt
@pytest.fixture(scope="module")
def stream_file(tmp_path_factory):
    src = tmp_path_factory.mktemp("stream") / "stream.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=90",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=90", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-g", "30", "-c:a", "aac", "-f", "mpegts", str(src)], check=True)
    return src


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def test_recorder_keeps_segments_and_cuts_a_scene(tmp_path, stream_file):
    rec = live.Recorder("https://www.twitch.tv/papaplatte", tmp_path / "rec", resolve=lambda url, h: str(stream_file))
    rec.start()
    rec.proc.wait(60)
    segs = rec.segments()
    assert len(segs) >= 12 and abs(segs[-1][2] - segs[0][1] - 90) < 2
    t0 = segs[0][1]
    path, first, length = rec.cut(t0 + 30, t0 + 70, tmp_path / "szene.mp4")
    assert first <= t0 + 30 and 40 <= length <= 55 and abs(duration(path) - length) < 1.5
    rec.prune(t0 + 90 + live.KEEP_SECONDS - 30, keep=live.KEEP_SECONDS)
    assert 0 < len(rec.segments()) < len(segs)  # alte Stuecke werden geloescht


class FakeRecorder:
    def __init__(self, segs):
        self.segs, self.runs, self.cuts = segs, 1, []

    def start(self):
        pass

    def alive(self):
        return True

    def stop(self):
        pass

    def segments(self):
        return self.segs

    def covered_until(self):
        return self.segs[-1][2]

    def prune(self, now, keep=0):
        pass

    def cut(self, a, b, out):
        self.cuts.append((a, b))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return out, a - 3, b - a + 3


def test_session_turns_a_chat_peak_into_a_queued_scene(tmp_path):
    creator = {"id": "papaplatte", "name": "Papaplatte", "url": "https://www.twitch.tv/papaplatte"}
    rec = FakeRecorder([(tmp_path / "s1.ts", 5000.0, 5600.0)])
    now = [5000.0]
    session = live.Session(creator, creator["url"], "Chill-Stream", s(work_dir=tmp_path), lambda: State(tmp_path / "db.sqlite"),
                           recorder=rec, chat=lambda stop: None, clock=lambda: now[0])
    for t in range(5000, 5300):
        session.on_message(t + 0.5, "na")
    for t in range(5300, 5306):
        for _ in range(10):
            session.on_message(t + 0.3, "KEKW 😂")
    session.step(5306)
    assert not session.pending and session.cuts == 1  # der Mitschnitt reicht schon bis nach dem Peak
    assert rec.cuts == [(5300 - 40 - 10, 5300 + 12 + 10)]
    st = State(tmp_path / "db.sqlite")
    row = st.videos_by_status(("queued",))[0]
    note = json.loads(row["note"])
    assert row["kind"] == "livecut" and row["creator"] == "papaplatte" and Path(row["url"]).exists()
    assert note["start"] == 13 and note["end"] == 13 + 52 and note["laughs"] == 60 and note["mode"] == "chat"
    assert st.get_meta("live_session:papaplatte")
    status = session.status()
    assert status["mode"] == "chat" and status["cuts"] == 1 and status["name"] == "Papaplatte"


def test_session_falls_back_to_loudness_without_chat(tmp_path, monkeypatch):
    creator = {"id": "kai", "name": "Kai", "url": "https://kick.com/kai"}
    segs = [(tmp_path / f"s{i}.ts", 100.0 + 6 * i, 106.0 + 6 * i) for i in range(60)]
    rec = FakeRecorder(segs)
    monkeypatch.setattr(live, "loudness_db", lambda path: [-10.0] * 6 if path.name == "s40.ts" else [-40.0] * 6)
    session = live.Session(creator, creator["url"], "", s(work_dir=tmp_path), lambda: State(tmp_path / "db.sqlite"),
                           recorder=rec, clock=lambda: 100.0)
    assert session.mode == "laut"  # Kick: kein Chat lesbar
    session.step(100 + 6 * 41 + 1)
    assert session.cuts == 1 and rec.cuts[0][0] == 100 + 6 * 40 - 40 - 10


# ---------------------------------------------------------------- Clip rendern und Vault
def test_live_scene_becomes_a_vault_clip(tmp_path, monkeypatch, stream_file):
    monkeypatch.setattr(run.transcribe, "transcribe_range", fake_words)
    raw = tmp_path / "szene.ts"
    raw.write_bytes(stream_file.read_bytes())
    creator = {"id": "papaplatte", "name": "Papaplatte", "url": "https://www.twitch.tv/papaplatte"}
    st = State(tmp_path / "db.sqlite")
    note = {"start": 15, "end": 55, "duration": 90, "score": 80, "base": 9, "laughs": 31, "mode": "chat",
            "stream_title": "Chill-Stream"}
    st.mark_video("live-papaplatte-1", "", "Chill-Stream", "queued", creator="papaplatte", url=str(raw),
                  kind="livecut", note=json.dumps(note))
    row = dict(st.get_video("live-papaplatte-1"))
    made = run.process_livecut([channel(creators=["papaplatte"])], s(work_dir=tmp_path, live_compilation=False), st,
                              row, creator)
    clip = st.clips()[0]
    assert made == 1 and clip["review"] == "vault" and clip["kind"] == "live" and clip["creator"] == "Papaplatte"
    assert "31x Lachen" in clip["reason"] and 30 <= clip["end"] - clip["start"] <= 55
    assert Path(st.get_clip(clip["clip_id"])["path"]).exists() and not raw.exists()
    assert st.video_status("live-papaplatte-1") == "done" and st.vault_counts() == {"clips": 1}


@pytest.fixture
def vault(tmp_path):
    accounts = []
    for name, platform in (("yt1", "youtube"), ("tt1", "tiktok")):
        (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
        accounts.append({"id": name, "platform": platform, "label": name, "token_file": str(tmp_path / f"{name}.json")})
    creator = {"id": "papaplatte", "name": "Papaplatte", "url": "https://www.twitch.tv/papaplatte", "auto_clip": True}
    cfg = {"settings": s(work_dir=tmp_path, dry_run=False, autopilot=True, vault_size=3), "accounts": accounts,
           "creators": [creator], "channels": [channel(creators=["papaplatte"], accounts=["yt1", "tt1"])]}
    st = State(tmp_path / "db.sqlite")

    def add(n):
        for i in range(n):
            path = tmp_path / f"live{len(st.clips())}.mp4"
            path.write_bytes(b"x")
            st.add_clip(path.stem, "cut", "clips", path, path.stem, "C", 0, 50, review="vault", kind="live")
            time.sleep(0.01)
    return cfg, st, add, lambda: State(tmp_path / "db.sqlite")


def test_full_vault_is_posted_on_every_account_at_once(vault, monkeypatch):
    cfg, st, add, open_state = vault
    uploaded = []
    monkeypatch.setattr(run, "upload_one", lambda account, clip, settings, tags=None:
                        uploaded.append((account["id"], clip["clip_id"])) or "remote")
    add(2)
    assert auto.release_vaults(cfg, st) == [] and auto.next_task(cfg, open_state) is None  # erst 2 von 3
    add(1)
    label, fn = auto.next_task(cfg, open_state)
    assert label.startswith("Vault posten: 6 Uploads") and st.vault_counts() == {}
    assert fn() is True
    assert sorted(uploaded) == sorted((a, f"live{i}") for a in ("yt1", "tt1") for i in range(3))
    assert auto.next_task(cfg, open_state) is None
    assert any("Vault voll" in n["title"] for n in st.notifications())


def test_vault_retries_an_account_later_when_youtube_says_no(vault, monkeypatch):
    cfg, st, add, open_state = vault

    def upload(account, clip, settings, tags=None):
        if account["id"] == "yt1" and clip["clip_id"] == "live1":
            raise run.UploadError("YouTube: quotaExceeded")
        return "remote"
    monkeypatch.setattr(run, "upload_one", upload)
    add(3)
    label, fn = auto.next_task(cfg, open_state)
    assert fn() is False
    posts = {(r["account"], r["clip_id"]): r["status"] for r in st.db.execute("SELECT * FROM posts")}
    assert posts[("yt1", "live0")] == "ok" and posts[("yt1", "live1")] == "failed" and ("yt1", "live2") not in posts
    assert all(posts[("tt1", f"live{i}")] == "ok" for i in range(3))
    assert auto.next_task(cfg, open_state) is None  # YouTube erst spaeter wieder
    later = datetime.fromtimestamp(time.time() + auto.VAULT_RETRY_SECONDS + 60)
    label, fn = auto.next_task(cfg, open_state, later)
    assert label.startswith("Vault posten: 2 Uploads (yt1)")


def test_vault_waits_in_probe_mode_and_can_be_released_by_hand(vault, monkeypatch):
    cfg, st, add, open_state = vault
    cfg["settings"]["dry_run"] = True
    add(3)
    assert auto.next_task(cfg, open_state) is None and st.vault_counts() == {}  # freigegeben, aber nichts hochgeladen
    cfg["settings"]["vault_size"] = 0
    add(1)
    assert auto.release_vaults(cfg, st) == [] and st.vault_counts() == {"clips": 1}


def test_livecut_in_queue_is_rendered_by_the_automation(vault, monkeypatch):
    cfg, st, add, open_state = vault
    st.mark_video("live-papaplatte-1", "", "Chill", "queued", creator="papaplatte", url="x", kind="livecut", note="{}")
    seen = []
    monkeypatch.setattr(run, "process_livecut", lambda channels, settings, state, row, creator:
                        seen.append((row["video_id"], creator["id"])) or 1)
    label, fn = auto.next_task(cfg, open_state)
    assert label == "Live-Clip rendern: Papaplatte"
    fn()
    assert seen == [("live-papaplatte-1", "papaplatte")]


# ---------------------------------------------------------------- Wer wird mitgeschnitten?
def test_only_live_creators_with_automation_are_recorded(vault):
    cfg, st, add, open_state = vault
    cfg["creators"].append({"id": "gtasty", "name": "Gtasty", "url": "https://www.youtube.com/@Gtasty", "auto_clip": True})
    watched = {"papaplatte": {"live": 1, "live_url": "https://www.twitch.tv/papaplatte", "live_title": "Chill"},
               "gtasty": {"live": 1, "live_url": "https://www.youtube.com/watch?v=abc", "live_title": "IRL"}}
    wanted = live.wanted_streams(cfg, watched)
    assert set(wanted) == {"papaplatte"}  # Gtasty gehoert zu keinem Kanal
    cfg["channels"][0]["creators"].append("gtasty")
    assert live.wanted_streams(cfg, watched)["gtasty"][1] == "https://www.youtube.com/watch?v=abc"
    cfg["settings"]["live_max_streams"] = 1
    assert len(live.wanted_streams(cfg, watched)) == 1
    cfg["settings"]["autopilot"] = False
    assert live.wanted_streams(cfg, watched) == {}


class FakeSession:
    def __init__(self, creator, url, title, settings, open_state):
        self.creator, self.url, self.alive, self.error = creator, url, False, ""

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def stop(self):
        self.alive = False

    def status(self):
        return {"creator": self.creator["id"]}


def test_manager_starts_and_stops_recordings(vault):
    cfg, st, add, open_state = vault
    manager = live.LiveManager(lambda settings: open_state(), session_factory=FakeSession)
    manager.sync(cfg)
    assert manager.status() == []
    st.watch_set("papaplatte", live=1, live_url="https://www.twitch.tv/papaplatte", live_title="Chill")
    manager.sync(cfg)
    session = manager.sessions["papaplatte"]
    assert manager.status() == [{"creator": "papaplatte"}] and session.alive
    st.watch_set("papaplatte", live=0)
    manager.sync(cfg)
    assert manager.status() == [] and not session.alive
    st.watch_set("papaplatte", live=1)
    manager.sync(cfg)
    broken = manager.sessions["papaplatte"]
    broken.alive, broken.error = False, "Mitschnitt startet nicht"
    manager.sync(cfg)
    assert manager.status() == []  # nach einem Fehler nicht sofort wieder versuchen


def test_recording_is_not_clipped_again_after_live_clips(vault, monkeypatch):
    cfg, st, add, open_state = vault
    st.set_meta("live_session:papaplatte", str(time.time() - 600))
    check = watch._Check(cfg, cfg["creators"][0], st, time.time())
    check.new_video({"id": "v1", "title": "Stream", "url": "u"}, was_stream=True)
    assert st.video_status("v1") == "seen"
    check.new_video({"id": "v2", "title": "Video", "url": "u"}, was_stream=False)
    assert st.video_status("v2") == "queued"
    cfg["channels"][0]["bestof"] = "daily"  # Best-ofs brauchen die Aufzeichnung trotzdem
    check.new_video({"id": "v3", "title": "Stream", "url": "u"}, was_stream=True)
    assert st.video_status("v3") == "queued"


def test_live_settings_are_checked():
    raw = {"settings": s(live_sensitivity="extrem"), "channels": [channel()], "accounts": [], "creators": []}
    with pytest.raises(config.ConfigError):
        config.validate(raw)
    raw["settings"] = s(live_before_seconds=200)
    with pytest.raises(config.ConfigError):
        config.validate(raw)
