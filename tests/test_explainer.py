import json
import re
import shutil

import pytest

from clipbot import ai, config
from clipbot.explainer import compose, pipeline, script as scripting, trends, voice
from clipbot.state import State

CHANNEL = {"name": "wissen", "kind": "explainer", "credit": "", "upload": {}}

SCRIPT = {
    "title": "Plastik verschluckt",
    "scenes": [
        {"kind": "hook", "voice": "Was passiert, wenn du Plastik verschluckst?", "headline": "Plastik verschluckt?",
         "sub": "", "emoji": "🧃", "number": "", "items": []},
        {"kind": "number", "voice": "Dein Darm ist etwa sieben Meter lang.", "headline": "Langer Weg",
         "sub": "", "emoji": "📏", "number": "7 m", "items": []},
        {"kind": "steps", "voice": "Magen, Dünndarm, Dickdarm. Dann ist es wieder draußen.", "headline": "Der Weg",
         "sub": "", "emoji": "➡️", "number": "", "items": ["Magen", "Dünndarm", "Dickdarm"]},
        {"kind": "question", "voice": "Hast du schon mal was verschluckt?", "headline": "Und du?",
         "sub": "", "emoji": "💬", "number": "", "items": []},
    ],
    "youtube_title": "Was passiert, wenn du Plastik verschluckst?",
    "caption": "Kleine Teile kommen meist von allein wieder raus. Wusstest du das?",
    "hashtags": ["#wissen", "körper", "#Wissen", "fakten"],
}


@pytest.fixture
def fake_ai(monkeypatch):
    answers = []
    monkeypatch.setitem(ai._CALLS, "fake", lambda *a: answers.pop(0))
    return answers


def s(tmp_path, **kw):
    return {**config.DEFAULTS, "ai_backend": "fake", "voice_backend": "stumm", "work_dir": tmp_path, **kw}


# ---------------------------------------------------------------- Stimme und Zeiten
def test_full_text_and_ranges():
    text, ranges = voice.full_text([{"voice": "Hallo  Welt."}, {"voice": "Zwei"}])
    assert text == "Hallo Welt. Zwei" and ranges == [(0, 11), (12, 16)]


def test_words_and_scene_times_from_alignment():
    text, ranges = voice.full_text([{"voice": "Eins zwei."}, {"voice": "Drei"}])
    starts = [i * 0.1 for i in range(len(text))]
    ends = [t + 0.1 for t in starts]
    words = voice.words_from_alignment(text, starts, ends)
    assert [w.text for _, w in words] == ["Eins", "zwei.", "Drei"]
    assert words[2][1].start == pytest.approx(1.1)
    times = voice.scene_times(words, ranges, 2.0)
    assert times == [(0.0, pytest.approx(1.1)), (pytest.approx(1.1), 2.0)]


def test_elevenlabs_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(voice.VoiceError, match="ELEVENLABS_API_KEY"):
        voice.elevenlabs("Hallo", tmp_path / "v.mp3", "x", "y")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg fehlt")
def test_silent_voice(tmp_path):
    out = voice.speak({"voice_backend": "stumm"}, SCRIPT["scenes"], tmp_path)
    assert out["audio"].exists() and out["duration"] > 5
    assert len(out["scenes"]) == 4 and out["scenes"][0][0] == 0.0
    assert all(a < b for a, b in out["scenes"])


# ---------------------------------------------------------------- Skript und Faktencheck
def test_write_normalizes_hashtags(fake_ai, tmp_path):
    fake_ai.append(json.dumps(SCRIPT))
    sc = scripting.write(s(tmp_path), "Plastik verschluckt")
    assert sc["hashtags"] == ["#wissen", "#körper", "#fakten"]


def test_fact_check_revises_when_wrong(fake_ai, tmp_path, monkeypatch):
    wrong = {"verdict": "falsch", "issues": [{"scene": 1, "claim": "7 m", "problem": "Eher 5 bis 7 m.", "fix": "fünf bis sieben Meter"}]}
    fake_ai.append(json.dumps(wrong))
    check = scripting.fact_check(s(tmp_path), SCRIPT)
    assert "FEHLER GEFUNDEN" in check["summary"] and "Szene 2" in check["summary"]
    fake_ai.append(json.dumps(SCRIPT))
    assert scripting.revise(s(tmp_path), SCRIPT, check)["title"] == "Plastik verschluckt"
    assert scripting.revise(s(tmp_path), SCRIPT, {"verdict": "ok", "issues": []}) is SCRIPT


def test_ideas_from_trends(fake_ai, tmp_path):
    fake_ai.append(json.dumps({"ideas": [{"topic": "Was passiert, wenn du nie schläfst?", "hook": "Nach drei Tagen…", "why": "Was-passiert-Videos laufen"}]}))
    ideas = trends.ideas(s(tmp_path), [{"title": "Was passiert wenn", "views": 1200000}], ["Körper"])
    assert ideas[0]["topic"].startswith("Was passiert")


# ---------------------------------------------------------------- Animation
def test_build_project_structure(tmp_path):
    words = [voice.Word(i * 0.4, i * 0.4 + 0.3, w) for i, w in enumerate("eins zwei drei vier fünf sechs".split())]
    spoken = {"audio": tmp_path / "assets" / "voice.mp3", "duration": 8.0, "words": words,
              "scenes": [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (6.0, 8.0)]}
    spoken["audio"].parent.mkdir(parents=True)
    spoken["audio"].write_bytes(b"")
    compose.build_project(tmp_path, SCRIPT, spoken)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'data-width="1080"' in html and 'data-height="1920"' in html
    assert (tmp_path / "assets" / "gsap.min.js").exists() and "assets/gsap.min.js" in html
    for i in range(1, 5):
        assert f'id="s{i}"' in html
    assert "7 m" in (tmp_path / "compositions" / "s2.html").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "script.json").read_text(encoding="utf-8"))["title"] == "Plastik verschluckt"
    assert "<script" not in "".join(sc["headline"] for sc in SCRIPT["scenes"])


def test_scene_text_is_escaped(tmp_path):
    scene = {**SCRIPT["scenes"][0], "headline": "<script>alert(1)</script>"}
    html = compose.scene_html("s1", scene, 2.0, compose.THEMES[0])
    assert "<script>alert" not in html and "&lt;script&gt;" in html


# ---------------------------------------------------------------- Ganzer Ablauf (ohne echtes Rendern)
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg fehlt")
def test_create_puts_video_into_review(fake_ai, tmp_path, monkeypatch):
    rendered = []

    def fake_render(project, out, cmd, quality="looks"):
        rendered.append(project)
        out.write_bytes(b"mp4")
        return out

    monkeypatch.setattr(compose, "render", fake_render)
    fake_ai += [json.dumps(SCRIPT), json.dumps({"verdict": "ok", "issues": []})]
    st = State(tmp_path / "state.sqlite")
    clip_id = pipeline.create(s(tmp_path), CHANNEL, st, "Plastik verschluckt")
    clip = st.get_clip(clip_id)
    assert clip["review"] == "wartet" and clip["reason"] == "Faktencheck: keine Beanstandung."
    assert State.texts_for(clip, "youtube")[0].endswith("#shorts")
    assert "#körper" in State.texts_for(clip, "tiktok")[1]
    assert (rendered[0] / "index.html").exists()
    assert re.match(r"exp-\d{8}-\d{6}-[0-9a-f]{6}$", clip_id)


def test_create_needs_ai(tmp_path):
    with pytest.raises(ai.AIError):
        pipeline.create(s(tmp_path, ai_backend="off"), CHANNEL, None, "Thema")


# ---------------------------------------------------------------- Konfiguration und Dashboard
def test_explainer_channel_needs_no_source():
    raw = {"settings": dict(config.DEFAULTS), "channels": [{"name": "wissen", "kind": "explainer"}]}
    config.validate(raw)
    raw["channels"][0]["kind"] = "clips"
    with pytest.raises(config.ConfigError):
        config.validate(raw)


def test_make_clips_skips_explainer_channel(tmp_path):
    import run
    assert run.make_clips({**CHANNEL, "source_url": ""}, s(tmp_path), None) == 0


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    import app
    cfg = {"settings": s(tmp_path), "channels": [CHANNEL]}
    monkeypatch.setattr(app.config, "load", lambda path=None, allow_empty=False: cfg)
    client = app.app.test_client()
    return lambda url, payload: client.post(url, json=payload, headers={"X-ClipBot": "1", "Host": "127.0.0.1:8765"}), app


def test_trends_endpoint(dashboard, fake_ai, monkeypatch):
    post, app = dashboard
    monkeypatch.setattr(trends, "find_trending", lambda queries, per_query=25: [{"title": "T", "views": 5, "query": queries[0]}])
    fake_ai.append(json.dumps({"ideas": [{"topic": "A?", "hook": "B", "why": "C"}]}))
    r = post("/api/explainer/trends", {"query": "geschichte"}).get_json()
    assert r["trending"][0]["query"] == "geschichte" and r["ideas"][0]["topic"] == "A?"


def test_create_endpoint_validates(dashboard, fake_ai, monkeypatch):
    post, app = dashboard
    assert post("/api/explainer/create", {"channel": "wissen", "topic": ""}).status_code == 400
    started = []
    monkeypatch.setattr(app, "start_job", lambda label, fn: started.append(label) or True)
    assert post("/api/explainer/create", {"channel": "wissen", "topic": "Warum ist der Himmel blau?"}).status_code == 200
    assert started == ["Erklaervideo: Warum ist der Himmel blau?"]
