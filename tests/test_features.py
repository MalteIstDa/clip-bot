"""Konten, Watchlist, Zeitplan, Best-ofs und Sprachen. Alles ohne Netz: Quellen, KI und Uploads sind Attrappen."""
import json
import sqlite3
import subprocess
import time
from datetime import datetime

import pytest

import run
from clipbot import ai, auto, bestof, config, lang, notify, schedule, sources, subtitles, viral, watch
from clipbot.state import State
from clipbot.transcribe import Word


def s(**kw):
    return {**config.DEFAULTS, **kw}


def words(text, start=0.0, step=0.4):
    return [Word(start + i * step, start + i * step + 0.3, t) for i, t in enumerate(text.split())]


@pytest.fixture
def fake_ai(monkeypatch):
    answers = []
    monkeypatch.setitem(ai._CALLS, "fake", lambda *a: answers.pop(0))
    return answers


@pytest.fixture(autouse=True)
def no_push(monkeypatch):
    """Keine echten Mitteilungen aus Tests."""
    sent = []
    monkeypatch.setattr(notify, "_push", lambda settings, title, text, url: sent.append(title))
    monkeypatch.setattr(run, "UPLOAD_PAUSE", 0)
    return sent


def channel(**kw):
    return {"name": "clips", "kind": "clips", "language": "de", "creators": [], "accounts": [], "tags": [],
            "credit": "", "title_template": "{hook} #shorts", "caption_template": "{hook}", "clips": True,
            "bestof": "off", "distribute": "alle", **kw}


# ---------------------------------------------------------------- Konfiguration
OLD_CONFIG = """
settings: {dry_run: true}
channels:
  - name: n3on
    source_url: https://www.youtube.com/@N3on
    credit: N3on
    caption_language: Englisch
    upload:
      youtube: {token_file: tokens/n3on_youtube.json}
      tiktok: {token_file: tokens/n3on_tiktok.json}
  - name: wissen
    kind: explainer
"""


def test_old_config_is_migrated(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(OLD_CONFIG, encoding="utf-8")
    raw = config.load_raw(path)
    config.validate(raw)
    assert [a["id"] for a in raw["accounts"]] == ["n3on_youtube", "n3on_tiktok"]
    assert raw["accounts"][0]["token_file"] == "tokens/n3on_youtube.json"  # Anmeldung bleibt gueltig
    assert raw["creators"] == [{"id": "n3on", "name": "N3on", "url": "https://www.youtube.com/@N3on",
                                "notify": True, "auto_clip": True}]
    n3on, wissen = raw["channels"]
    assert n3on["creators"] == ["n3on"] and n3on["accounts"] == ["n3on_youtube", "n3on_tiktok"]
    assert n3on["language"] == "en" and wissen["language"] == "de" and "upload" not in n3on
    config.save(raw, path)
    again = config.load_raw(path)
    assert again["accounts"] == raw["accounts"] and again["channels"][0]["creators"] == ["n3on"]
    assert (tmp_path / "config.yaml.vor-konten.bak").read_text(encoding="utf-8") == OLD_CONFIG


def test_config_rejects_unknown_links_and_bad_times():
    raw = {"settings": dict(config.DEFAULTS), "accounts": [], "creators": [],
           "channels": [channel(accounts=["fehlt"])]}
    with pytest.raises(config.ConfigError, match="Konto 'fehlt'"):
        config.validate(raw)
    raw = {"settings": {**config.DEFAULTS, "upload_times": "25:00"}, "accounts": [], "creators": [], "channels": []}
    with pytest.raises(config.ConfigError, match="keine Uhrzeit"):
        config.validate(raw, allow_empty=True)


def test_languages():
    assert lang.code("Französisch") == "fr" and lang.code("pl") == "pl" and lang.code("") == "en"
    assert lang.get("Spanisch")["prompt"] == "Spanisch"  # unbekannte Sprache: Name geht an die KI
    assert lang.get("pl")["live_best"] == "Najlepsze momenty"


# ---------------------------------------------------------------- Zeitplan
def test_parse_times():
    assert schedule.parse_times("20:00, 9:30;12.00  9:30") == [(9, 30), (12, 0), (20, 0)]
    assert schedule.parse_times("") == []
    with pytest.raises(ValueError):
        schedule.parse_times("mittags")


def test_open_and_next_slot():
    times = [(12, 0), (23, 30)]
    assert schedule.open_slot(times, datetime(2026, 11, 16, 11, 59), 60) is None
    assert schedule.open_slot(times, datetime(2026, 11, 16, 12, 40), 60) == datetime(2026, 11, 16, 12, 0)
    assert schedule.open_slot(times, datetime(2026, 11, 16, 13, 0), 60) is None
    assert schedule.open_slot(times, datetime(2026, 11, 17, 0, 10), 60) == datetime(2026, 11, 16, 23, 30)
    assert schedule.next_slot(times, datetime(2026, 11, 16, 23, 45)) == datetime(2026, 11, 17, 12, 0)


def test_bestof_period():
    monday = datetime(2026, 11, 16, 19, 0)
    assert schedule.bestof_period("daily", monday, "18:00", 6) == "2026-11-16"
    assert schedule.bestof_period("daily", monday.replace(hour=17), "18:00", 6) is None
    assert schedule.bestof_period("weekly", monday, "18:00", 6) is None            # erst am Sonntag
    assert schedule.bestof_period("weekly", datetime(2026, 11, 22, 18, 5), "18:00", 6) == "2026-W47"


# ---------------------------------------------------------------- Datenbank: Uploads je Konto
def test_old_uploads_move_to_accounts(tmp_path):
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.executescript("""
        CREATE TABLE videos (video_id TEXT PRIMARY KEY, channel TEXT, title TEXT, status TEXT, ts REAL);
        CREATE TABLE clips (clip_id TEXT PRIMARY KEY, video_id TEXT, channel TEXT, path TEXT,
            title TEXT, caption TEXT, start REAL, end REAL, ts REAL);
        CREATE TABLE uploads (clip_id TEXT, platform TEXT, status TEXT, remote_id TEXT, error TEXT, ts REAL,
            PRIMARY KEY (clip_id, platform));
        INSERT INTO videos VALUES ('v', 'n3on', 'Stream', 'done', 1);
        INSERT INTO clips VALUES ('a', 'v', 'n3on', 'a.mp4', 'T', 'C', 0, 60, 1);
        INSERT INTO uploads VALUES ('a', 'youtube', 'ok', 'abc', NULL, 5);
    """)
    db.commit(); db.close()
    st = State(tmp_path / "old.sqlite")
    assert st.clips()[0]["uploads"] == {"n3on_youtube": {"platform": "youtube", "status": "ok", "remote_id": "abc",
                                                         "error": None, "ts": 5}}
    assert st.video_done("v") and st.get_video("v")["creator"] is None


def test_pending_per_account_and_rotation(tmp_path):
    st = State(tmp_path / "db.sqlite")
    for clip_id in ("a", "b"):
        st.add_clip(clip_id, "v", "clips", f"{clip_id}.mp4", clip_id, "C", 0, 60, review="freigegeben")
    st.mark_post("a", "yt1", "youtube", "ok", "id1")
    ids = lambda *args, **kw: [r["clip_id"] for r in st.pending_clips(*args, **kw)]
    assert ids("clips", "yt1") == ["b"] and ids("clips", "yt2") == ["a", "b"]
    assert ids("clips", "yt2", "youtube", exclusive=True) == ["b"]      # a hat schon ein anderes YouTube-Konto
    assert ids("clips", "tt1", "tiktok", exclusive=True) == ["a", "b"]  # andere Plattform zaehlt nicht
    st.mark_post("b", "yt2", "youtube", "failed", error="x")
    assert ids("clips", "yt2") == ["a", "b"] and st.posts_since("yt2", time.time() - 5) == 1


def test_mark_video_keeps_details(tmp_path):
    st = State(tmp_path / "db.sqlite")
    st.mark_video("v1", "", "Titel", "queued", creator="gtasty", url="https://x/v1", kind="video")
    st.mark_video("v1", "clips", "", "done")
    row = st.get_video("v1")
    assert (row["status"], row["creator"], row["url"], row["title"], row["channel"]) == \
        ("done", "gtasty", "https://x/v1", "Titel", "clips")


# ---------------------------------------------------------------- Quellen
FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <entry><yt:videoId>new1</yt:videoId><title>Neues Video</title><published>2026-11-16T18:00:07+00:00</published></entry>
 <entry><yt:videoId>old1</yt:videoId><title>Altes &amp; Gutes</title><published>2026-11-01T10:00:00+00:00</published></entry>
</feed>"""


def test_parse_feed_and_urls():
    entries = sources.parse_feed(FEED)
    assert [e["id"] for e in entries] == ["new1", "old1"] and entries[1]["title"] == "Altes & Gutes"
    assert entries[0]["url"] == "https://www.youtube.com/watch?v=new1" and entries[0]["published"] > 1.7e9
    assert sources.platform_of("https://www.twitch.tv/papaplatte") == "twitch"
    assert sources.platform_of("https://kick.com/n3on") == "kick"
    assert sources.clean_url("https://www.youtube.com/@Gtasty/videos?view=0") == "https://www.youtube.com/@Gtasty"
    assert sources.name_from_url("https://www.youtube.com/@Gtasty/streams") == "Gtasty"
    assert sources.videos_url("https://www.twitch.tv/papaplatte/videos").endswith("/papaplatte/videos?filter=archives&sort=time")
    assert sources.youtube_channel_id("https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA") == "UCX6OQ3DkcsbYNE6H8uQQuVA"


# ---------------------------------------------------------------- Watchlist
class FakeNet:
    """Ersetzt die Netzzugriffe von clipbot.sources."""

    def __init__(self, monkeypatch):
        self.feed, self.status, self.streams, self.live, self.latest = [], {}, [], None, []
        monkeypatch.setattr(sources, "youtube_channel_id", lambda url: "UCtest")
        monkeypatch.setattr(sources, "youtube_feed", lambda cid: list(self.feed))
        monkeypatch.setattr(sources, "youtube_streams", lambda url, limit=5: list(self.streams))
        monkeypatch.setattr(sources, "probe", self.probe)
        monkeypatch.setattr(sources, "live_now", lambda url: self.live)
        monkeypatch.setattr(sources, "list_latest", lambda url, limit: list(self.latest))

    def probe(self, url):
        video_id = url.rsplit("=", 1)[-1]
        if video_id not in self.status:
            raise sources.SourceError("Video unavailable")
        live_status, duration = self.status[video_id]
        return {"id": video_id, "title": f"Titel {video_id}", "live_status": live_status, "duration": duration,
                "uploader": "Gtasty", "url": url}


def entry(video_id, age=60.0):
    return {"id": video_id, "title": f"Titel {video_id}", "url": f"https://www.youtube.com/watch?v={video_id}",
            "published": time.time() - age}


@pytest.fixture
def watched(tmp_path, monkeypatch):
    net = FakeNet(monkeypatch)
    creator = {"id": "gtasty", "name": "Gtasty", "url": "https://www.youtube.com/@Gtasty", "notify": True,
               "auto_clip": True}
    cfg = {"settings": s(work_dir=tmp_path), "accounts": [], "creators": [creator],
           "channels": [channel(creators=["gtasty"])]}
    st = State(tmp_path / "db.sqlite")
    titles = lambda: [n["title"] for n in reversed(st.notifications())]
    return net, cfg, creator, st, titles


def test_first_check_only_remembers(watched):
    net, cfg, creator, st, titles = watched
    net.feed = [entry("a"), entry("b")]
    assert watch.check_creator(cfg, creator, st)
    assert titles() == [] and st.video_status("a") == "seen" and st.watch_get("gtasty")["baseline"] == 1


def test_new_video_reminds_and_queues(watched):
    net, cfg, creator, st, titles = watched
    net.feed = [entry("a")]
    watch.check_creator(cfg, creator, st)
    net.feed = [entry("short"), entry("neu"), entry("alt", age=10 * 86400), entry("a")]
    net.status = {"neu": ("not_live", 900), "short": ("not_live", 45)}
    watch.check_creator(cfg, creator, st)
    assert titles() == ["Gtasty hat ein neues Video"]
    assert st.video_status("neu") == "queued" and st.get_video("neu")["creator"] == "gtasty"
    assert st.video_status("short") == "skipped" and st.video_status("alt") == "seen"
    watch.check_creator(cfg, creator, st)  # zweiter Durchgang: kein doppelter Reminder
    assert len(titles()) == 1


def test_stream_from_announcement_to_recording(watched):
    net, cfg, creator, st, titles = watched
    watch.check_creator(cfg, creator, st)
    net.feed = [entry("live1")]
    net.status = {"live1": ("is_upcoming", None)}
    watch.check_creator(cfg, creator, st)
    assert st.video_status("live1") == "upcoming" and titles() == []
    net.status["live1"] = ("is_live", None)
    watch.check_creator(cfg, creator, st)
    watch.check_creator(cfg, creator, st)
    assert titles() == ["Gtasty ist live"] and st.watch_get("gtasty")["live"] == 1
    net.status["live1"] = ("post_live", None)
    watch.check_creator(cfg, creator, st)
    assert st.video_status("live1") == "processing" and st.watch_get("gtasty")["live"] == 0
    net.status["live1"] = ("was_live", 7200)
    watch.check_creator(cfg, creator, st)
    assert st.video_status("live1") == "queued" and titles()[-1] == "Gtasty: Stream-Aufzeichnung ist da"


def test_running_stream_is_found_on_first_check(watched):
    net, cfg, creator, st, titles = watched
    net.streams = [{"id": "s1", "title": "Jetzt live", "url": "https://www.youtube.com/watch?v=s1",
                    "live_status": "is_live"}]
    watch.check_creator(cfg, creator, st)
    assert titles() == ["Gtasty ist live"] and st.video_status("s1") == "live"


def test_without_auto_clip_nothing_is_queued(watched):
    net, cfg, creator, st, titles = watched
    creator["auto_clip"] = False
    watch.check_creator(cfg, creator, st)
    net.feed, net.status = [entry("neu")], {"neu": ("not_live", 900)}
    watch.check_creator(cfg, creator, st)
    assert st.video_status("neu") == "seen" and titles() == ["Gtasty hat ein neues Video"]
    creator["notify"] = False
    net.feed, net.status = [entry("neu2")], {"neu2": ("not_live", 900)}
    watch.check_creator(cfg, creator, st)
    assert len(titles()) == 1  # Reminder abgeschaltet


def test_twitch_live_and_recording(watched):
    net, cfg, creator, st, titles = watched
    creator["url"] = "https://www.twitch.tv/gtasty"
    net.latest = [{"id": "v1", "title": "Alter Stream", "url": "https://www.twitch.tv/videos/1"}]
    watch.check_creator(cfg, creator, st)
    assert titles() == [] and st.video_status("v1") == "seen"
    net.live = {"id": "x", "title": "IRL in Berlin", "url": "https://www.twitch.tv/gtasty"}
    net.latest = [{"id": "v2", "title": "IRL in Berlin", "url": "https://www.twitch.tv/videos/2"}] + net.latest
    watch.check_creator(cfg, creator, st)
    watch.check_creator(cfg, creator, st)
    assert titles() == ["Gtasty ist live"] and st.video_status("v2") is None  # Aufzeichnung waechst noch
    net.live = None
    watch.check_creator(cfg, creator, st)
    assert st.video_status("v2") == "queued" and st.watch_get("gtasty")["live"] == 0
    assert titles()[-1] == "Gtasty: Stream-Aufzeichnung ist da"


def test_errors_are_kept_for_the_dashboard(watched, monkeypatch):
    net, cfg, creator, st, _ = watched
    monkeypatch.setattr(sources, "youtube_channel_id", lambda url: (_ for _ in ()).throw(RuntimeError("kein Netz")))
    assert watch.check_creator(cfg, creator, st) is False
    assert st.watch_get("gtasty")["error"] == "kein Netz"


def test_push_goes_out_for_live(watched, no_push, monkeypatch):
    net, cfg, creator, st, _ = watched
    notify.send(st, cfg["settings"], "live", "Gtasty ist live", background=False)
    notify.send(st, cfg["settings"], "upload", "Hochgeladen", background=False)  # nur im Dashboard
    assert no_push == ["Gtasty ist live"] and st.unread_count() == 2
    st.mark_read()
    assert st.unread_count() == 0


# ---------------------------------------------------------------- Automatik
@pytest.fixture
def autopilot(tmp_path, monkeypatch):
    for name in ("yt1", "yt2"):
        (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    accounts = [{"id": "yt1", "platform": "youtube", "label": "Eins", "token_file": str(tmp_path / "yt1.json"), "times": ""},
                {"id": "yt2", "platform": "youtube", "label": "Zwei", "token_file": str(tmp_path / "yt2.json"), "times": "20:00"}]
    creator = {"id": "gtasty", "name": "Gtasty", "url": "https://www.youtube.com/@Gtasty", "notify": True, "auto_clip": True}
    cfg = {"settings": s(work_dir=tmp_path, dry_run=False, upload_times="12:00", autopilot=True),
           "accounts": accounts, "creators": [creator],
           "channels": [channel(creators=["gtasty"], accounts=["yt1", "yt2"])]}
    st = State(tmp_path / "db.sqlite")
    for clip_id in ("a", "b"):
        path = tmp_path / f"{clip_id}.mp4"
        path.write_bytes(b"x")
        st.add_clip(clip_id, "v", "clips", path, clip_id.upper(), "C", 0, 60, review="freigegeben")
        time.sleep(0.01)
    uploaded = []
    monkeypatch.setattr(run, "upload_one", lambda account, clip, settings, tags=None:
                        uploaded.append((account["id"], clip["clip_id"])) or "remote")
    return cfg, st, uploaded, lambda: State(tmp_path / "db.sqlite")


def at(hour, minute=0):
    return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_uploads_only_in_time_windows(autopilot):
    cfg, st, uploaded, open_state = autopilot
    assert auto.due_uploads(cfg, st, at(11, 0)) == []
    due = auto.due_uploads(cfg, st, at(12, 10))
    assert [(a["id"], c["clip_id"]) for a, _, c in due] == [("yt1", "a")]   # yt2 hat eigene Zeiten (20:00)
    label, job = auto.next_task(cfg, open_state, at(12, 10))
    assert label == "Upload nach Zeitplan (Eins)" and job() is True and uploaded == [("yt1", "a")]


def test_one_upload_per_window(autopilot, monkeypatch):
    cfg, st, uploaded, open_state = autopilot
    monkeypatch.setattr(time, "time", lambda: at(12, 5).timestamp())
    auto.next_task(cfg, open_state, at(12, 5))[1]()
    assert auto.next_task(cfg, open_state, at(12, 30)) is None      # dieses Fenster ist verbraucht
    monkeypatch.setattr(time, "time", lambda: at(20, 1).timestamp())
    assert [(a["id"], c["clip_id"]) for a, _, c in auto.due_uploads(cfg, st, at(20, 1))] == [("yt2", "a")]


def test_rotation_gives_each_account_another_clip(autopilot):
    cfg, st, uploaded, open_state = autopilot
    cfg["channels"][0]["distribute"] = "abwechselnd"
    cfg["accounts"][1]["times"] = ""
    due = auto.due_uploads(cfg, st, at(12, 1))
    assert [(a["id"], c["clip_id"]) for a, _, c in due] == [("yt1", "a"), ("yt2", "b")]


def test_dry_run_and_missing_login_block_uploads(autopilot, tmp_path):
    cfg, st, uploaded, open_state = autopilot
    cfg["settings"]["dry_run"] = True
    assert auto.next_task(cfg, open_state, at(12, 10)) is None
    cfg["settings"]["dry_run"] = False
    (tmp_path / "yt1.json").unlink()
    assert auto.due_uploads(cfg, st, at(12, 10)) == []


def test_queued_video_is_clipped_for_all_channels(autopilot, monkeypatch):
    cfg, st, uploaded, open_state = autopilot
    cfg["settings"]["upload_times"] = ""
    cfg["accounts"][1]["times"] = ""
    cfg["channels"].append(channel(name="frankreich", language="fr", creators=["gtasty"]))
    st.mark_video("neu", "", "Wilder Stunt", "queued", creator="gtasty", url="https://x/neu")
    st.mark_video("fremd", "", "Ohne Creator", "queued", creator="geloescht", url="https://x/fremd")
    seen = []

    def fake_process(channels, settings, state, video, creator=None):
        seen.append(([c["name"] for c in channels], video["id"], creator["id"]))
        state.mark_video(video["id"], channels[0]["name"], video["title"], "done")
        return 3

    monkeypatch.setattr(run, "process_video", fake_process)
    label, job = auto.next_task(cfg, open_state, at(9, 0))
    assert label.startswith("Automatisch clippen: Gtasty")
    job()
    assert seen == [(["clips", "frankreich"], "neu", "gtasty")] and st.video_status("neu") == "done"
    assert st.notifications()[0]["title"] == "3 neue Clips von Gtasty"
    assert auto.next_task(cfg, open_state, at(9, 0)) is None and st.video_status("fremd") == "seen"


def test_stale_queue_entries_are_dropped(autopilot):
    """War die Automatik lange aus, wird nicht tagelang Altes nachgeclippt."""
    cfg, st, uploaded, open_state = autopilot
    cfg["settings"]["upload_times"] = ""
    cfg["accounts"][1]["times"] = ""
    st.mark_video("alt", "", "Von letzter Woche", "queued", creator="gtasty", url="https://x/alt")
    st.db.execute("UPDATE videos SET ts=? WHERE video_id='alt'", (time.time() - 5 * 86400,))
    st.db.commit()
    assert auto.next_task(cfg, open_state, datetime.now()) is None and st.video_status("alt") == "skipped"


def test_failed_clip_job_is_not_retried(autopilot, monkeypatch):
    cfg, st, uploaded, open_state = autopilot
    cfg["settings"]["upload_times"] = ""
    cfg["accounts"][1]["times"] = ""
    st.mark_video("neu", "", "Titel", "queued", creator="gtasty", url="https://x/neu")
    monkeypatch.setattr(run, "process_video", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Platte voll")))
    with pytest.raises(RuntimeError):
        auto.next_task(cfg, open_state, at(9, 0))[1]()
    assert st.video_status("neu") == "failed" and st.get_video("neu")["note"] == "Platte voll"
    assert auto.next_task(cfg, open_state, at(9, 0)) is None


def test_bestof_is_started_once_per_period(autopilot, monkeypatch):
    cfg, st, uploaded, open_state = autopilot
    cfg["settings"]["upload_times"] = ""
    cfg["accounts"][1]["times"] = ""
    cfg["channels"][0]["bestof"] = "daily"
    built = []
    monkeypatch.setattr(bestof, "build", lambda cfg, ch, state, period=None, force=False: built.append(period))
    assert auto.next_task(cfg, open_state, at(17, 0)) is None
    label, job = auto.next_task(cfg, open_state, at(18, 30))
    assert label == "Best of des Tages: clips"
    job()
    assert built == ["daily"] and auto.next_task(cfg, open_state, at(19, 0)) is None


# ---------------------------------------------------------------- Creator-Tag oben im Bild
def test_headline_names_creator_without_ai():
    texts = viral.write_texts(s(), channel(), "Video", "Hallo zusammen. Mehr Text", 1, creator="Gtasty")
    assert texts["hook_overlay"] == "Gtasty"
    assert viral.write_texts(s(creator_tag=False), channel(), "Video", "Hallo.", 1, creator="Gtasty")["hook_overlay"] == ""


def test_ai_is_told_to_name_the_creator(monkeypatch):
    prompts = []

    def fake(settings, system, prompt, schema):
        prompts.append(prompt)
        return json.dumps({"hook_overlay": "Gtastys unglaublicher Stunt", "youtube_title": "T", "youtube_description": "D",
                           "tiktok_caption": "C", "instagram_caption": "I", "hashtags_youtube": [], "hashtags_tiktok": [],
                           "hashtags_instagram": []})

    monkeypatch.setitem(ai._CALLS, "fake", fake)
    texts = viral.write_texts(s(ai_backend="fake"), channel(language="fr"), "Live", "transcript", 1, creator="Gtasty")
    assert "Nenne darin Gtasty beim Namen" in prompts[0] and "auf Französisch" in prompts[0]
    assert texts["hook_overlay"] == "Gtastys unglaublicher Stunt"
    assert "Crédit" not in texts["platforms"]["youtube"]["caption"]  # keine Quellenzeile mehr


def test_headline_stays_for_whole_clip(tmp_path):
    out = subtitles.write_ass(words("eins zwei drei"), 0, 12, tmp_path / "a.ass", hook="Gtastys Stunt", hook_seconds=0)
    assert r"Dialogue: 1,0:00:00.00,0:00:12.00,Hook,,0,0,0,,{\fad(150,0)}GTASTYS STUNT" in out.read_text(encoding="utf-8")
    out = subtitles.write_ass(words("eins zwei drei"), 0, 12, tmp_path / "b.ass", hook="Gtastys Stunt", hook_seconds=3)
    assert "0:00:03.00,Hook" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------- Best-of
def moment(n, creator, duration, score, text="Text"):
    return {"moment_id": f"m{n}", "creator": creator, "creator_name": creator.title(), "duration": duration,
            "score": score, "text": text, "video_title": "Stream", "words": [], "path": ""}


def test_select_prefers_strong_and_varied_moments():
    moments = [moment(1, "a", 20, 9), moment(2, "a", 20, 8), moment(3, "a", 20, 7), moment(4, "b", 20, 5),
               moment(5, "c", 20, 4), moment(6, "d", 30, 3)]
    chosen = bestof.select(moments, 60, 90, per_creator=2)
    assert [m["moment_id"] for m in chosen] == ["m1", "m4", "m5", "m2"]  # bester zuerst, zweitbester zum Schluss
    assert sum(m["duration"] for m in chosen) <= 90
    few = bestof.select([moment(1, "a", 25, 9), moment(2, "a", 25, 8), moment(3, "a", 25, 7)], 60, 120, per_creator=1)
    assert len(few) == 3  # zu wenig Creator: die Grenze je Creator wird gelockert


def test_plan_without_ai_uses_language_of_channel():
    moments = [moment(1, "gtasty", 30, 9), moment(2, "n3on", 30, 8), moment(3, "speed", 30, 7)]
    todo = bestof.plan(s(), channel(language="en", tags=["irl"]), moments, "weekly")
    assert todo["title"] == "Best of the Week: Gtasty, Speed and more #shorts"  # Namen in der Reihenfolge im Video
    assert [h for _, h in todo["picks"]] == ["Gtasty", "Speed", "N3On"]
    assert "Credits" not in todo["platforms"]["youtube"]["caption"]  # keine Quellenzeile (Wunsch von Malte)
    assert "#shorts" in todo["platforms"]["youtube"]["caption"] and "#irl" in todo["caption"]


def test_plan_with_ai_keeps_limits(fake_ai):
    moments = [moment(1, "gtasty", 40, 9), moment(2, "n3on", 40, 8), moment(3, "speed", 40, 7), moment(4, "kai", 30, 6)]
    fake_ai.append(json.dumps({
        "picks": [{"index": 1, "headline": "N3on rastet aus"}, {"index": 9, "headline": "gibt es nicht"},
                  {"index": 0, "headline": "Gtastys unglaublicher Stunt"}, {"index": 2, "headline": "zu lang"}],
        "youtube_title": "Diese Woche war wild", "youtube_description": "Die besten Momente.",
        "tiktok_caption": "Welcher war am besten?", "hashtags": ["bestof", "#n3on"]}))
    todo = bestof.plan(s(ai_backend="fake", bestof_max_seconds=100), channel(), moments, "weekly")
    assert [(m["moment_id"], h) for m, h in todo["picks"]] == [("m2", "N3on rastet aus"), ("m1", "Gtastys unglaublicher Stunt")]
    assert todo["title"] == "Diese Woche war wild #shorts" and "#n3on" not in todo["caption"]  # kein #streamername


def test_plan_fills_up_when_ai_picks_too_little(fake_ai):
    moments = [moment(1, "gtasty", 25, 9), moment(2, "n3on", 25, 8), moment(3, "speed", 25, 7)]
    fake_ai.append(json.dumps({"picks": [{"index": 0, "headline": "Gtastys Stunt"}], "youtube_title": "T",
                               "youtube_description": "D", "tiktok_caption": "C", "hashtags": []}))
    todo = bestof.plan(s(ai_backend="fake"), channel(), moments, "daily")
    assert sum(m["duration"] for m, _ in todo["picks"]) >= 60 and todo["picks"][0][1] == "Gtastys Stunt"


def test_plan_limits_creators_even_if_ai_ignores_it(fake_ai):
    moments = [moment(1, "gtasty", 15, 9), moment(2, "gtasty", 15, 8), moment(3, "gtasty", 15, 7),
               moment(4, "gtasty", 15, 6), moment(5, "n3on", 15, 5), moment(6, "speed", 15, 4)]
    fake_ai.append(json.dumps({"picks": [{"index": i, "headline": f"Zeile {i}"} for i in range(4)], "youtube_title": "T",
                               "youtube_description": "D", "tiktok_caption": "C", "hashtags": []}))
    todo = bestof.plan(s(ai_backend="fake", bestof_max_per_creator=2), channel(), moments, "daily")
    ids = [m["moment_id"] for m, _ in todo["picks"]]
    assert ids[0] == "m1" and sorted(ids) == ["m1", "m2", "m5", "m6"]  # nur 2x Gtasty, aufgefuellt mit anderen


@pytest.fixture(scope="module")
def source_video(tmp_path_factory):
    src = tmp_path_factory.mktemp("quelle") / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=200",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=200", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    return src


def test_moments_and_bestof_end_to_end(tmp_path, monkeypatch, source_video):
    """Zwei Videos von zwei Creatorn -> Momente -> ein Best-of (echtes ffmpeg)."""
    monkeypatch.setattr(run.transcribe, "transcribe_range",
                        lambda path, a, b, *r: words(" ".join(f"w{i}." if i % 6 == 5 else f"w{i}" for i in range(int((b - a) / 0.4))), a))
    creators = [{"id": "gtasty", "name": "Gtasty", "url": "https://www.youtube.com/@gtasty"},
                {"id": "n3on", "name": "N3on", "url": "https://www.youtube.com/@n3on"}]
    ch = channel(name="bestof", clips=False, bestof="daily", creators=["gtasty", "n3on"])
    settings = s(work_dir=tmp_path, moments_per_video=2, moment_min_seconds=6, moment_max_seconds=10,
                 bestof_min_seconds=20, bestof_max_seconds=40, skip_start_seconds=5, skip_end_seconds=5)
    cfg = {"settings": settings, "accounts": [], "creators": creators, "channels": [ch]}
    st = State(tmp_path / "db.sqlite")
    for creator in creators:
        info = {"id": f"vid_{creator['id']}", "title": f"Stream von {creator['name']}", "duration": 200.0,
                "heatmap": [], "uploader": "x"}
        monkeypatch.setattr(run.download, "download", lambda *a, info=info: (source_video, info))
        monkeypatch.setitem(settings, "keep_source_videos", True)
        assert run.process_video([ch], settings, st, {"id": info["id"], "title": info["title"], "url": "x"}, creator) == 0
    moments = bestof.available(cfg, ch, st, "daily")
    assert len(moments) == 4 and {m["creator_name"] for m in moments} == {"Gtasty", "N3on"}
    assert all(6 <= m["duration"] <= 10.5 and m["words"] for m in moments) and st.clips() == []

    clip_id = bestof.build(cfg, ch, st)
    clip = st.clips()[0]
    assert clip["clip_id"] == clip_id and clip["kind"] == "bestof" and clip["review"] == "wartet"
    assert clip["title"].startswith("Best of des Tages: ") and set(clip["creator"].split(", ")) == {"Gtasty", "N3on"}
    path = st.get_clip(clip_id)["path"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration",
                            "-of", "json", path], capture_output=True, text=True)
    data = json.loads(probe.stdout)
    assert {"width": 1080, "height": 1920} in data["streams"]
    assert abs(float(data["format"]["duration"]) - clip["end"]) < 1.5 and 20 <= clip["end"] <= 41
    assert bestof.available(cfg, ch, st, "daily") == []     # Momente sind verbraucht
    assert bestof.build(cfg, ch, st) is None                 # nichts Neues: kein zweites Best-of
    st.delete_clip(clip_id)
    assert len(bestof.available(cfg, ch, st, "daily")) == 4  # geloeschtes Best-of gibt die Momente wieder frei


def test_old_moments_are_removed(tmp_path):
    st = State(tmp_path / "db.sqlite")
    path = tmp_path / "_momente" / "v" / "m.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    st.add_moment("m", "v", "gtasty", "Gtasty", "Stream", path, 10, 1.0, "text", [])
    assert bestof.cleanup(st, now=time.time() + 5 * 86400) == 0 and path.exists()
    assert bestof.cleanup(st, now=time.time() + 20 * 86400) == 1 and not path.parent.exists()


# ---------------------------------------------------------------- Lauf: mehrere Creator, mehrere Konten
def test_make_clips_checks_every_creator(tmp_path, monkeypatch):
    creators = [{"id": "a", "name": "A", "url": "https://www.youtube.com/@a"},
                {"id": "b", "name": "B", "url": "https://www.twitch.tv/b"}]
    ch = channel(creators=["a", "b"])
    cfg = {"settings": s(work_dir=tmp_path), "accounts": [], "creators": creators, "channels": [ch]}
    monkeypatch.setattr(run.download, "list_latest", lambda url, limit: [{"id": "v" + url[-1], "title": "T", "url": url}])
    done = []
    monkeypatch.setattr(run, "process_video", lambda channels, settings, state, video, creator=None:
                        done.append((creator["id"], video["id"])) or 2)
    st = State(tmp_path / "db.sqlite")
    assert run.make_clips(ch, cfg["settings"], st, cfg) == 4 and done == [("a", "va"), ("b", "vb")]
    assert run.make_clips(channel(), cfg["settings"], st, cfg) == 0  # Kanal ohne Creator


def test_upload_clips_goes_round_robin(tmp_path, monkeypatch):
    st = State(tmp_path / "db.sqlite")
    for clip_id in ("a", "b", "c"):
        path = tmp_path / f"{clip_id}.mp4"
        path.write_bytes(b"x")
        st.add_clip(clip_id, "v", "clips", path, clip_id, "C", 0, 60, review="freigegeben")
        time.sleep(0.01)
    accounts = [{"id": "yt1", "platform": "youtube", "label": "Eins"}, {"id": "yt2", "platform": "youtube", "label": "Zwei"}]
    order = []

    def fake_upload(account, clip, settings, tags=None):
        if clip["clip_id"] == "c" and account["id"] == "yt2":
            raise run.UploadError("Limit erreicht")
        order.append((account["id"], clip["clip_id"]))
        return "remote"

    monkeypatch.setattr(run, "upload_one", fake_upload)
    run.upload_clips(channel(distribute="abwechselnd"), s(), st, accounts)
    assert order == [("yt1", "a"), ("yt2", "b"), ("yt1", "c")]
    order.clear()
    run.upload_clips(channel(), s(), st, accounts)            # 'alle': jedes Konto holt nach, was ihm fehlt
    assert order == [("yt1", "b"), ("yt2", "a")]
    assert st.clips()[0]["uploads"]["yt2"]["status"] == "failed"  # c auf yt2: Fehler bleibt sichtbar


# ---------------------------------------------------------------- Dashboard-Schnittstellen
@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text("settings: {work_dir: data}\n", encoding="utf-8")
    monkeypatch.setattr(app.autopilot, "check_now", lambda: None)
    client = app.app.test_client()
    headers = {"X-ClipBot": "1", "Host": "127.0.0.1:8765"}
    post = lambda url, payload=None: client.post(url, json=payload or {}, headers=headers)
    state = lambda: client.get("/api/state", headers=headers).get_json()
    return post, state, tmp_path, app


def test_accounts_creators_and_channels_in_dashboard(dashboard):
    post, state, tmp, app = dashboard
    assert post("/api/accounts", {"platform": "youtube", "label": "Daily Clips DE", "times": "12:00, 18:00"}).get_json()["id"] == "yt_daily_clips_de"
    assert post("/api/accounts", {"platform": "tiktok", "label": "Daily Clips DE"}).status_code == 200
    assert post("/api/accounts", {"platform": "youtube", "label": "X", "times": "99:99"}).status_code == 400
    r = post("/api/creators", {"urls": "https://www.youtube.com/@Gtasty/videos\nhttps://www.twitch.tv/papaplatte\n@N3on\nhttps://www.youtube.com/@gtasty"})
    assert r.get_json() == {"ok": True, "added": 3, "skipped": 1}
    r = post("/api/channels", {"is_new": True, "name": "bestof_de", "language": "Deutsch", "bestof": "weekly",
                               "distribute": "abwechselnd", "creators": ["gtasty", "papaplatte", "gibt-es-nicht"],
                               "accounts": ["yt_daily_clips_de", "tt_daily_clips_de"]})
    assert r.status_code == 200
    data = state()
    ch = data["channels"][0]
    assert ch["creators"] == ["gtasty", "papaplatte"] and ch["language"] == "de" and ch["bestof"] == "weekly"
    yt = data["accounts"][0]
    assert yt["effective_times"] == "12:00, 18:00" and yt["channels"] == ["bestof_de"] and not yt["connected"]
    assert yt["next_slot"] and data["accounts"][1]["effective_times"] == ""
    names = {c["id"]: c for c in data["creators"]}
    assert names["papaplatte"]["platform"] == "twitch" and names["n3on"]["url"] == "https://www.youtube.com/@N3on"
    assert names["gtasty"]["channels"] == ["bestof_de"] and names["n3on"]["channels"] == []

    assert post("/api/creators/n3on", {"name": "N3on", "notify": False, "channels": ["bestof_de"]}).status_code == 200
    assert post("/api/creators/gtasty/delete").status_code == 200
    assert post("/api/accounts/tt_daily_clips_de/delete").status_code == 200
    data = state()
    assert data["channels"][0]["creators"] == ["papaplatte", "n3on"] and data["channels"][0]["accounts"] == ["yt_daily_clips_de"]
    assert [c["notify"] for c in data["creators"] if c["id"] == "n3on"] == [False]


def test_channel_name_with_spaces_and_umlauts_is_converted(dashboard):
    post, state, tmp, app = dashboard
    r = post("/api/channels", {"is_new": True, "name": "  Deutsche Größen & Streamer! "})
    assert r.status_code == 200 and r.get_json()["name"] == "deutsche-groessen-streamer"
    assert [c["name"] for c in state()["channels"]] == ["deutsche-groessen-streamer"]
    r = post("/api/channels", {"is_new": True, "name": "Deutsche Größen Streamer"})
    assert r.status_code == 400 and "gibt es schon" in r.get_json()["error"]
    r = post("/api/channels", {"is_new": True, "name": " !! "})
    assert r.status_code == 400 and "Namen" in r.get_json()["error"]


def test_autopilot_switch_and_notifications(dashboard):
    post, state, tmp, app = dashboard
    assert state()["auto"]["enabled"] is False
    assert post("/api/autopilot", {"on": True}).get_json()["on"] is True
    st = app.open_state(config.load(allow_empty=True)["settings"])
    st.add_notification("live", "Gtasty ist live", "IRL", "https://x")
    st.mark_video("v", "", "T", "queued", creator="gtasty", url="u")
    st.close()
    data = state()
    assert data["auto"] == {**data["auto"], "enabled": True, "queued": 1} and data["unread"] == 1
    assert data["notifications"][0]["title"] == "Gtasty ist live"
    post("/api/notifications/read")
    assert state()["unread"] == 0


def test_upload_button_uses_the_chosen_account(dashboard, monkeypatch):
    post, state, tmp, app = dashboard
    post("/api/accounts", {"platform": "youtube", "label": "Eins"})
    post("/api/accounts", {"platform": "youtube", "label": "Zwei"})
    post("/api/channels", {"is_new": True, "name": "clips", "accounts": ["yt_eins"]})
    st = app.open_state(config.load(allow_empty=True)["settings"])
    st.add_clip("a", "v", "clips", tmp / "a.mp4", "A", "C", 0, 60)
    st.close()
    jobs, uploaded = [], []
    monkeypatch.setattr(app, "start_job", lambda label, fn: jobs.append(fn) or True)
    monkeypatch.setattr(run, "upload_one", lambda account, clip, settings, tags=None: uploaded.append(account["id"]) or "r1")
    assert post("/api/clips/a/upload", {"account": "yt_zwei"}).status_code == 400  # gehoert nicht zum Kanal
    assert post("/api/clips/a/upload", {"account": "yt_eins"}).status_code == 200
    assert jobs[0]() is True and uploaded == ["yt_eins"]
    clip = state()["clips"][0]
    assert clip["review"] == "freigegeben" and clip["uploads"]["yt_eins"]["remote_id"] == "r1"


def test_upload_waits_for_running_job_instead_of_refusing(dashboard, monkeypatch):
    """Laeuft gerade ein Auftrag (z. B. ein Live-Schnitt), steht der Upload an und startet danach."""
    import threading
    post, state, tmp, app = dashboard
    post("/api/accounts", {"platform": "youtube", "label": "Eins"})
    post("/api/channels", {"is_new": True, "name": "clips", "accounts": ["yt_eins"]})
    st = app.open_state(config.load(allow_empty=True)["settings"])
    st.add_clip("a", "v", "clips", tmp / "a.mp4", "A", "C", 0, 60)
    st.close()
    uploaded, done = [], threading.Event()
    monkeypatch.setattr(run, "upload_one", lambda account, clip, settings, tags=None: uploaded.append(account["id"]) or done.set() or "r1")
    gate = threading.Event()
    assert app.start_job("Live-Schnitt", lambda: gate.wait(5))
    r = post("/api/clips/a/upload", {"account": "yt_eins"})
    assert r.status_code == 200 and r.get_json()["status"] == "queued"
    assert post("/api/clips/a/upload", {"account": "yt_eins"}).get_json()["status"] == "queued"
    assert [q["key"] for q in state()["job"]["queue"]] == ["a|yt_eins"]  # nicht doppelt
    assert uploaded == []
    gate.set()
    assert done.wait(5) and uploaded == ["yt_eins"]
    for _ in range(50):
        if not app.job["running"]:
            break
        threading.Event().wait(0.05)
    assert not app.job["running"] and state()["job"]["queue"] == []


def test_bestof_button(dashboard, monkeypatch):
    post, state, tmp, app = dashboard
    post("/api/channels", {"is_new": True, "name": "clips", "bestof": "daily"})
    jobs, built = [], []
    monkeypatch.setattr(app, "start_job", lambda label, fn: jobs.append((label, fn)) or True)
    monkeypatch.setattr(bestof, "build", lambda cfg, ch, st, period=None, force=False: built.append((ch["name"], period, force)))
    assert post("/api/run", {"mode": "bestof", "channel": "clips", "period": "weekly"}).status_code == 200
    jobs[0][1]()
    assert jobs[0][0] == "Best-of bauen (clips)" and built == [("clips", "weekly", True)]


def test_dashboard_page_has_all_views(tmp_path, monkeypatch):
    """Die Oberflaeche wird ausgeliefert und kennt alle Bereiche und Schnittstellen, die sie aufruft."""
    import re

    import app as dashboard
    monkeypatch.setattr(config, "ROOT", tmp_path)
    client = dashboard.app.test_client()
    page = client.get("/", base_url=f"http://127.0.0.1:{dashboard.PORT}")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and '<canvas id="sky"' in html
    for view in ("overview", "review", "clips", "explainer", "watchlist", "live", "campaigns", "accounts", "channels",
                 "settings"):
        assert f'id="view-{view}"' in html and f'data-view="{view}"' in html
    routes = {re.sub(r"<[^>]+>", "*", str(rule)) for rule in dashboard.app.url_map.iter_rules()}
    called = {re.sub(r"\$\{[^}]+\}", "*", path) + ("*" if path.endswith("/") else "")
              for path in re.findall(r'api\([`"](/api/[^`"?]+)[`"]', html)}
    assert called and not {path for path in called if path not in routes}
    assert {"/api/creators", "/api/accounts", "/api/autopilot", "/api/watch/check", "/api/notifications/read"} <= called
    for key in re.findall(r'\["([a-z_]+)", "[^"]+", "(?:check|text|number|select)"', html):
        assert key in config.DEFAULTS, key  # jedes Feld der Einstellungen gibt es wirklich


def test_reminder_wording_for_one_clip(autopilot, monkeypatch):
    cfg, st, _, open_state = autopilot
    creator = cfg["creators"][0]
    monkeypatch.setattr(run, "process_video", lambda *a, **k: 1)
    st.mark_video("v1", "", "Video", "queued", creator=creator["id"], url="https://x")
    auto.run_clip(cfg, creator, {"id": "v1", "title": "Video", "url": "https://x"}, open_state)
    note = st.notifications()[0]
    assert note["title"] == "1 neuer Clip von Gtasty" and note["text"].startswith("Er wartet")
