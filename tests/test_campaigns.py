"""Clip-Kampagnen: finden, einzelne Clips mit Pflicht-Hashtag, sofort posten, Bereich im Dashboard."""
import json
import subprocess
import time

import pytest

import run
from clipbot import auto, campaigns, config, notify
from clipbot.state import State
from clipbot.transcribe import Word

GTASTY_PAGE = """<html><head><title>Gtasty Clips – Verdiene Geld mit Clips</title>
<style>.a{color:#fff} .b{background:#1a2b3c}</style></head><body>
<h1>Gtasty Clipping Kampagne</h1><p>Lade Clips von Gtasty hoch und verdiene pro 1.000 Aufrufe.</p>
<p>Pflicht: Nutze #gtasty in deiner Beschreibung. Auszahlung jeden Monat.</p>
<p>Folge uns #clipping #gtastyclips</p></body></html>"""


def s(**kw):
    return {**config.DEFAULTS, **kw}


def channel(**kw):
    return {"name": "clips", "kind": "clips", "language": "de", "creators": ["gtasty"], "accounts": [], "tags": [],
            "credit": "", "title_template": "{hook} #shorts", "caption_template": "{hook}", "clips": True,
            "bestof": "off", "distribute": "alle", **kw}


def fake_web(pages):
    """pages: {url: (status, final_url, html)}; alles andere ist nicht erreichbar."""
    calls = []

    def fetch(url):
        calls.append(url)
        if url not in pages:
            raise OSError("nicht erreichbar")
        return pages[url]
    fetch.calls = calls
    return fetch


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(notify, "_push", lambda *a: None)


def test_hashtags_are_added_once_to_every_caption():
    texts = {"title": "T", "caption": "Krass! #gta6 #Gtasty", "hook_overlay": "",
             "platforms": {"tiktok": {"caption": "Wahnsinn #fyp"}, "youtube": {"title": "Y", "caption": ""}}}
    out = campaigns.apply_tags(texts, {"hashtags": "gtasty, #GtastyClips #gtasty"})
    assert out["caption"] == "Krass! #gta6 #Gtasty\n\n#GtastyClips"
    assert out["platforms"]["tiktok"]["caption"] == "Wahnsinn #fyp\n\n#gtasty #GtastyClips"
    assert out["platforms"]["youtube"]["caption"] == ""  # leer = allgemeine Beschreibung gilt, die hat die Tags
    assert campaigns.clean_hashtags("gtasty  #x-y, #ok_1") == "#gtasty #ok_1"


def test_search_finds_the_campaign_site_and_its_hashtag():
    creator = {"id": "gtasty", "name": "Gtasty", "url": "https://www.twitch.tv/gtasty"}
    web = fake_web({"https://gtastyclips.com": (200, "https://gtastyclips.com/", GTASTY_PAGE)})
    hit = campaigns.search_creator(creator, web)
    assert hit["url"] == "https://gtastyclips.com" and hit["hashtags"] == "#gtasty #gtastyclips"
    assert hit["title"].startswith("Gtasty Clips") and web.calls[0] == "https://gtastyclips.com"


def test_parked_or_unrelated_pages_are_not_campaigns():
    creator = {"id": "papaplatte", "name": "Papaplatte", "url": "https://www.twitch.tv/papaplatte"}
    web = fake_web({
        "https://papaplatteclips.com": (200, "https://sedo.com/parked", "<p>Diese Domain kaufen</p>"),
        "https://papaplatteclipping.com": (200, "https://papaplatteclipping.com", "<p>Coming soon</p>"),
        "https://papaplatteclips.de": (404, "https://papaplatteclips.de", ""),
    })
    assert campaigns.search_creator(creator, web) is None


def test_discover_saves_campaign_notifies_and_waits_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    creators = [{"id": "gtasty", "name": "Gtasty", "url": "https://www.twitch.tv/gtasty"},
                {"id": "lacy", "name": "Lacy", "url": "https://www.twitch.tv/lacy"}]
    (tmp_path / "config.yaml").write_text(json.dumps({"settings": {"work_dir": "data"}, "creators": creators}),
                                          encoding="utf-8")
    cfg = config.load(allow_empty=True)
    st = State(tmp_path / "db.sqlite")
    web = fake_web({"https://gtastyclips.com": (200, "https://gtastyclips.com", GTASTY_PAGE)})
    new = campaigns.discover(cfg, st, web, now=1000.0)
    assert [c["creator"] for c in new] == ["gtasty"]
    saved = config.load(allow_empty=True)["campaigns"]
    assert saved == [{"id": "gtasty", "creator": "gtasty", "name": "Gtasty", "url": "https://gtastyclips.com",
                      "hashtags": "#gtasty #gtastyclips", "active": True, "found": "auto"}]
    assert st.notifications()[0]["title"] == "Clip-Kampagne gefunden: Gtasty"
    web.calls.clear()
    assert campaigns.discover(config.load(allow_empty=True), st, web, now=1000.0 + 3600) == [] and web.calls == []
    campaigns.discover(config.load(allow_empty=True), st, web, now=1000.0 + 90000)
    assert web.calls and all("lacy" in u for u in web.calls)  # Gtasty hat schon eine, nur Lacy wird neu gesucht


def fake_words(path, a, b, *rest):
    a = max(0.0, a)
    return [Word(a + i * 0.4, a + i * 0.4 + 0.3, f"w{i}." if i % 6 == 5 else f"w{i}") for i in range(int((b - a) / 0.4))]


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    src = tmp_path_factory.mktemp("szene") / "szene.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=40", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    return src


def test_live_scene_of_campaign_creator_becomes_single_tagged_clip(tmp_path, scene, monkeypatch):
    monkeypatch.setattr(run.transcribe, "transcribe_range", fake_words)
    st = State(tmp_path / "db.sqlite")
    raw = tmp_path / "szene.mp4"
    raw.write_bytes(scene.read_bytes())
    note = {"start": 8, "end": 30, "duration": 40, "peak": 26, "score": 40, "base": 10, "laughs": 2,
            "mode": "chat", "stream_title": "GTA RP", "category": "Grand Theft Auto VI"}
    st.mark_video("live-gtasty-1", "", "GTA RP", "queued", creator="gtasty", url=str(raw), kind="livecut",
                  note=json.dumps(note))
    creator = {"id": "gtasty", "name": "Gtasty", "url": "https://www.twitch.tv/gtasty"}
    campaign = {"id": "gtasty", "creator": "gtasty", "hashtags": "#gtasty", "active": True}
    made = run.process_livecut([channel()], s(work_dir=tmp_path, live_compilation=True), st,
                               dict(st.get_video("live-gtasty-1")), creator, campaign)
    assert made == 1  # sofort ein Clip, kein Sammeln fuer einen Zusammenschnitt
    clip = st.clips()[0]
    assert clip["kind"] == "campaign" and clip["campaign"] == "gtasty" and clip["review"] == "freigegeben"
    assert "#gtasty" in clip["caption"] and st.vault_counts() == {}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for name in ("tt1", "yt1", "ig1"):
        (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    accounts = [{"id": "tt1", "platform": "tiktok", "label": "TikTok", "token_file": str(tmp_path / "tt1.json"), "times": ""},
                {"id": "yt1", "platform": "youtube", "label": "YouTube", "token_file": str(tmp_path / "yt1.json"), "times": ""},
                {"id": "ig1", "platform": "instagram", "label": "Insta", "token_file": str(tmp_path / "ig1.json"),
                 "ig_user_id": "1", "token_env": "IG_TOKEN", "times": ""}]
    creator = {"id": "gtasty", "name": "Gtasty", "url": "https://www.twitch.tv/gtasty", "notify": True, "auto_clip": True}
    cfg = {"settings": s(work_dir=tmp_path, dry_run=False, autopilot=True), "accounts": accounts, "creators": [creator],
           "campaigns": [{"id": "gtasty", "creator": "gtasty", "hashtags": "#gtasty", "active": True}],
           "channels": [channel(accounts=["tt1", "yt1", "ig1"])]}
    st = State(tmp_path / "db.sqlite")
    path = tmp_path / "k.mp4"
    path.write_bytes(b"x")
    st.add_clip("k", "v", "clips", path, "K", "C #gtasty", 0, 30, review="freigegeben", kind="campaign",
                campaign="gtasty")
    uploaded = []
    monkeypatch.setattr(run, "upload_one", lambda account, clip, settings, tags=None:
                        uploaded.append((account["id"], clip["clip_id"])) or "remote")
    monkeypatch.setattr(run, "UPLOAD_PAUSE", 0)
    return cfg, st, uploaded, lambda: State(tmp_path / "db.sqlite")


def test_campaign_clips_are_posted_right_away_but_never_on_instagram(setup):
    cfg, st, uploaded, open_state = setup
    task = auto.next_task(cfg, open_state)
    assert task[0].startswith("Kampagnen-Clips posten: 2 Uploads")
    assert task[1]() is True and sorted(uploaded) == [("tt1", "k"), ("yt1", "k")]
    assert auto.next_task(cfg, open_state) is None


def test_no_campaign_posts_in_dry_run(setup):
    cfg, st, uploaded, open_state = setup
    cfg["settings"]["dry_run"] = True
    assert auto.next_task(cfg, open_state) is None


def test_campaigns_in_dashboard(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text("settings: {work_dir: data}\n", encoding="utf-8")
    monkeypatch.setattr(app.autopilot, "check_now", lambda: None)
    client = app.app.test_client()
    headers = {"X-ClipBot": "1", "Host": "127.0.0.1:8765"}
    post = lambda url, payload=None: client.post(url, json=payload or {}, headers=headers)
    post("/api/creators", {"urls": "https://www.twitch.tv/gtasty"})
    assert post("/api/campaigns", {"creator": "nicht-da"}).status_code == 400
    r = post("/api/campaigns", {"creator": "gtasty", "url": "gtastyclips.com"})
    assert r.status_code == 200
    data = client.get("/api/state", headers=headers).get_json()
    assert data["campaigns"] == [{"id": "gtasty", "creator": "gtasty", "name": "gtasty", "url": "https://gtastyclips.com",
                                  "hashtags": "#gtasty", "active": True, "found": "hand"}]
    assert post("/api/campaigns", {"creator": "gtasty"}).status_code == 400  # nur eine je Creator
    post("/api/campaigns", {"id": "gtasty", "hashtags": "gtasty #gtastyclips", "active": False})
    cp = client.get("/api/state", headers=headers).get_json()["campaigns"][0]
    assert cp["hashtags"] == "#gtasty #gtastyclips" and cp["active"] is False
    assert campaigns.of_creator(config.load(allow_empty=True), "gtasty") is None  # pausiert
    post("/api/creators/gtasty/delete")
    assert client.get("/api/state", headers=headers).get_json()["campaigns"] == []
