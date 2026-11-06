import json
import subprocess

import pytest

import run
from clipbot import ai, config, subtitles, viral
from clipbot.state import State
from clipbot.transcribe import Word

CHANNEL = {"name": "n3on", "title_template": "{hook} #n3on #shorts", "caption_template": "{hook}\n\nClip aus: {video_title}",
           "tags": ["n3on"], "credit": "N3on", "caption_language": "Englisch", "upload": {}}

AI_TEXTS = {
    "hook_overlay": "He did not expect this",
    "youtube_title": "N3on gets the shock of his life",
    "youtube_description": "Wait for the end.",
    "tiktok_caption": "Would you have stayed calm?",
    "instagram_caption": "This escalated fast.",
    "hashtags_youtube": ["#n3on", "irl stream", "#kick"],
    "hashtags_tiktok": ["fyp", "n3on", "streamer", "viral"],
    "hashtags_instagram": ["reels", "n3on", "funny", "streamer", "clips", "irl"],
}


def words(text, start=0.0, step=0.4):
    return [Word(start + i * step, start + i * step + 0.3, t) for i, t in enumerate(text.split())]


@pytest.fixture
def fake_ai(monkeypatch):
    answers = []
    monkeypatch.setitem(ai._CALLS, "fake", lambda *a: answers.pop(0))
    return answers


def s(**kw):
    return {**config.DEFAULTS, **kw}


# ---------------------------------------------------------------- Texte ohne KI
def test_short_hook_cuts_at_sentence():
    assert viral.short_hook("So tell us. What happened next was crazy", "x") == "So tell us."


def test_short_hook_never_cuts_mid_word():
    hook = viral.short_hook("So tell us a little bit about what we are doing in house today with everyone", "x", 40)
    assert hook.endswith("…") and len(hook) <= 41 and "abou…" not in hook


def test_template_texts_without_ai():
    texts = viral.write_texts(s(), CHANNEL, "Video", "Hello there. More text", 1)
    assert texts["title"] == "Hello there. #n3on #shorts" and texts["platforms"] == {} and texts["hook_overlay"] == ""


def test_normalize_hashtags():
    assert viral.normalize_hashtags(["#n3on", "irl stream", "N3ON", "#"], 5) == ["#n3on", "#irlstream"]


# ---------------------------------------------------------------- Texte mit KI
def test_ai_texts_per_platform(fake_ai):
    fake_ai.append(json.dumps(AI_TEXTS))
    texts = viral.write_texts(s(ai_backend="fake"), CHANNEL, "Stream 12", "transcript", 1)
    yt, tt, ig = (texts["platforms"][p]["caption"] for p in ("youtube", "tiktok", "instagram"))
    assert texts["platforms"]["youtube"]["title"].endswith("#shorts")
    assert "#shorts" in yt and "Credit: N3on" in yt and "Clip aus: Stream 12" in tt
    assert tt.startswith("Would you have stayed calm? #fyp")
    assert ig.count("#") <= 10 and "#reels" in ig
    assert texts["hook_overlay"] == "He did not expect this"


def test_ai_failure_falls_back_to_templates(fake_ai):
    fake_ai += ["kaputt", "auch kaputt"]
    texts = viral.write_texts(s(ai_backend="fake"), CHANNEL, "Video", "Hi there.", 1)
    assert texts["title"] == "Hi there. #n3on #shorts"


def test_pick_highlights_uses_ai_ranking(fake_ai):
    cands = [{"start": i * 100, "end": i * 100 + 90, "score": 10 - i, "text": f"c{i}"} for i in range(4)]
    fake_ai.append(json.dumps({"ranking": [
        {"index": 3, "viral_score": 95, "reason": "Pointe"}, {"index": 0, "viral_score": 40, "reason": "ok"},
        {"index": 7, "viral_score": 99, "reason": "gibt es nicht"}]}))
    picked = viral.pick_highlights(s(ai_backend="fake"), cands, 2, "Video")
    assert [p["start"] for p in picked] == [300, 0] and picked[0]["reason"] == "Pointe"


def test_pick_highlights_without_ai_uses_loudness():
    cands = [{"start": 0, "end": 90, "score": 1, "text": ""}, {"start": 200, "end": 290, "score": 5, "text": ""}]
    assert viral.pick_highlights(s(), cands, 1, "V")[0]["start"] == 200


# ---------------------------------------------------------------- Untertitel
def test_merge_hyphen_fragments():
    merged = subtitles.merge_fragments([Word(0, 0.2, "IN"), Word(0.2, 0.5, "-HOUSE"), Word(0.6, 0.8, "team")])
    assert [w.text for w in merged] == ["IN-HOUSE", "team"] and merged[0].end == 0.5


def test_hook_overlay_in_ass(tmp_path):
    out = subtitles.write_ass(words("one two three"), 0, 5, tmp_path / "s.ass", hook="He did not expect this at all")
    text = out.read_text(encoding="utf-8")
    assert "Style: Hook" in text and r"{\fad(150,250)}HE DID NOT EXPECT\NTHIS AT ALL" in text


# ---------------------------------------------------------------- Datenbank
def test_state_texts_and_manual_edit(tmp_path):
    st = State(tmp_path / "db.sqlite")
    st.add_clip("c1", "v", "n3on", "x.mp4", "T", "C", 0, 60, {"tiktok": {"title": "T", "caption": "TT"}}, "hook", "r")
    row = st.get_clip("c1")
    assert State.texts_for(row, "tiktok") == ("T", "TT") and State.texts_for(row, "youtube") == ("T", "C")
    st.update_clip("c1", "T", "C")  # unveraendert gespeichert: KI-Fassung bleibt
    assert State.texts_for(st.get_clip("c1"), "tiktok") == ("T", "TT")
    st.update_clip("c1", "T", "Neu")  # selbst geaendert: gilt fuer alle Plattformen
    assert State.texts_for(st.get_clip("c1"), "tiktok") == ("T", "Neu")


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.execute("CREATE TABLE clips (clip_id TEXT PRIMARY KEY, video_id TEXT, channel TEXT, path TEXT,"
               " title TEXT, caption TEXT, start REAL, end REAL, ts REAL)")
    db.execute("INSERT INTO clips VALUES ('a','v','n','p','t','c',0,1,0)")
    db.commit(); db.close()
    st = State(tmp_path / "old.sqlite")
    assert State.texts_for(st.get_clip("a"), "youtube") == ("t", "c")


# ---------------------------------------------------------------- Ganzer Clip-Lauf (echtes ffmpeg, Fake-KI)
def test_process_video_end_to_end(tmp_path, monkeypatch, fake_ai):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=200",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=200", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    info = {"id": "vid1", "title": "Test Stream", "duration": 200.0, "heatmap": []}
    monkeypatch.setattr(run.download, "download", lambda *a: (src, info))
    monkeypatch.setattr(run.transcribe, "transcribe_range",
                        lambda path, a, b, *r: words(" ".join(f"w{i}." if i % 8 == 7 else f"w{i}" for i in range(int((b - a) / 0.4))), a))
    fake_ai.append(json.dumps({"ranking": [{"index": 1, "viral_score": 90, "reason": "stark"},
                                           {"index": 0, "viral_score": 50, "reason": "mittel"}]}))
    fake_ai.append(json.dumps(AI_TEXTS))
    settings = s(ai_backend="fake", work_dir=tmp_path, clips_per_video=1, ai_candidates=2,
                 clip_min_seconds=20, clip_max_seconds=30, skip_start_seconds=5, skip_end_seconds=5)
    st = State(tmp_path / "db.sqlite")
    made = run.process_video(CHANNEL, settings, st, {"id": "vid1", "title": "Test Stream", "url": "x"})
    assert made == 1
    clip = st.clips()[0]
    assert clip["hook"] == "He did not expect this" and clip["reason"] == "stark" and clip["review"] == "wartet"
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", clip["path"]], capture_output=True, text=True)
    assert probe.stdout.strip() == "1080,1920"
    assert "Hook" in (tmp_path / "n3on" / "clips" / "vid1_1" / "subs.ass").read_text(encoding="utf-8")


# ---------------------------------------------------------------- Freigabe (Phase 3)
def test_only_approved_clips_are_pending(tmp_path):
    st = State(tmp_path / "db.sqlite")
    st.add_clip("a", "v", "n3on", "a.mp4", "A", "C", 0, 60)                       # wartet
    st.add_clip("b", "v", "n3on", "b.mp4", "B", "C", 0, 60, review="freigegeben")
    st.add_clip("c", "v", "n3on", "c.mp4", "C", "C", 0, 60, review="verworfen")
    assert [r["clip_id"] for r in st.pending_clips("n3on", "youtube")] == ["b"]
    st.set_review("a", "freigegeben")
    assert {r["clip_id"] for r in st.pending_clips("n3on", "youtube")} == {"a", "b"}
    with pytest.raises(ValueError):
        st.set_review("a", "quatsch")


def test_clips_payload_parses_texts_and_hides_transcript(tmp_path):
    st = State(tmp_path / "db.sqlite")
    st.add_clip("a", "v", "n3on", "a.mp4", "A", "C", 0, 60, {"tiktok": {"title": "A", "caption": "X"}},
                transcript="geheim lang")
    clip = st.clips()[0]
    assert clip["texts"]["tiktok"]["caption"] == "X" and "transcript" not in clip and clip["review"] == "wartet"


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    import app
    settings = {**config.DEFAULTS, "work_dir": tmp_path, "ai_backend": "fake"}
    cfg = {"settings": settings, "channels": [CHANNEL]}
    monkeypatch.setattr(app.config, "load", lambda path=None, allow_empty=False: cfg)
    st = State(tmp_path / "state.sqlite")
    st.mark_video("v", "n3on", "Stream 12", "done")
    st.add_clip("a", "v", "n3on", str(tmp_path / "a.mp4"), "A", "C", 0, 60, transcript="Hello there")
    st.close()
    client = app.app.test_client()
    post = lambda url, payload: client.post(url, json=payload, headers={"X-ClipBot": "1", "Host": "127.0.0.1:8765"})
    return post, tmp_path


def test_review_endpoints(dashboard, fake_ai):
    post, tmp = dashboard
    assert post("/api/clips/a/review", {"action": "approve"}).status_code == 200
    assert State(tmp / "state.sqlite").get_clip("a")["review"] == "freigegeben"
    assert post("/api/clips/a/review", {"action": "nix"}).status_code == 400
    r = post("/api/clips/a/texts", {"platforms": {"youtube": {"title": "YT", "caption": "yt"},
                                                  "tiktok": {"title": "YT", "caption": "tt #fyp"}}})
    assert r.status_code == 200
    row = State(tmp / "state.sqlite").get_clip("a")
    assert State.texts_for(row, "tiktok") == ("YT", "tt #fyp") and row["title"] == "YT"
    fake_ai.append(json.dumps(AI_TEXTS))
    assert post("/api/clips/a/retext", {}).status_code == 200
    row = State(tmp / "state.sqlite").get_clip("a")
    assert State.texts_for(row, "youtube")[0].startswith("N3on gets the shock")
