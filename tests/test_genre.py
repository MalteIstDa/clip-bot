"""Art des Streams erkennen (Gaming, Just Chatting, IRL) und Format, Hook und Hashtags anpassen. Ohne Netz."""
import json

import pytest

import run
from clipbot import ai, config, genre, live, viral
from clipbot.state import State
from clipbot.transcribe import Word


def s(**kw):
    return {**config.DEFAULTS, **kw}


def channel(**kw):
    return {"name": "clips", "kind": "clips", "language": "de", "creators": [], "accounts": [], "tags": [],
            "credit": "", "title_template": "{hook} #shorts", "caption_template": "{hook}", "clips": True,
            "bestof": "off", "distribute": "alle", **kw}


@pytest.mark.parametrize("category,title,expected", [
    ("Just Chatting", "", "chatting"),
    ("IRL", "", "irl"),
    ("Travel & Outdoors", "", "irl"),
    ("Fortnite", "", "gaming"),
    ("Grand Theft Auto V", "RP mit den Jungs", "gaming"),
    ("Gaming", "", "gaming"),
    ("People & Blogs", "IRL durch Tokyo", "irl"),
    ("People & Blogs", "Q&A mit euch", "chatting"),
    ("Talk Shows & Podcasts", "", "chatting"),
    (None, "Ranked bis Platin", "gaming"),
    (None, "unterwegs in Berlin", "irl"),
    (None, "Guten Morgen", None),
])
def test_categories_are_sorted_into_three_kinds(category, title, expected):
    assert genre.classify(category, title) == expected


def test_tags_layout_and_label_per_kind():
    assert genre.tags_for("gaming", "Fortnite") == ["fortnite", "gaming", "twitchclips"]
    assert genre.tags_for("gaming", "Gaming") == ["gaming", "twitchclips"]
    assert genre.tags_for("chatting", "Just Chatting") == ["justchatting", "streamer"]
    assert genre.tags_for(None, None) == []
    assert genre.describe("gaming", "Elden Ring") == "Gaming (Elden Ring)" and genre.describe("irl", "IRL") == "IRL"
    base = s()
    assert genre.settings_for(base, "gaming")["layout"] == "blur"
    assert genre.settings_for(base, "chatting")["layout"] == "crop" and genre.settings_for(base, None) is base
    assert genre.settings_for(s(genre_formats=False), "chatting")["layout"] == "blur"


def test_category_from_youtube_and_twitch_chapters():
    assert genre.category_from_info({"categories": ["Gaming"], "extractor_key": "Youtube"}) == "Gaming"
    vod = {"extractor_key": "TwitchVod", "chapters": [
        {"start_time": 0, "end_time": 1800, "title": "Just Chatting"},
        {"start_time": 1800, "end_time": 9000, "title": "Fortnite"}]}
    assert genre.category_from_info(vod, 600) == "Just Chatting" and genre.category_from_info(vod, 4000) == "Fortnite"
    assert genre.category_from_info(vod) == "Fortnite" and genre.category_from_info({}) is None


def test_ai_gets_the_kind_and_game_hashtags_are_added(monkeypatch):
    prompts = []
    answer = {"hook_overlay": "Papaplatte gewinnt mit 1 HP", "youtube_title": "Mit 1 HP gewonnen",
              "youtube_description": "Knapper geht's nicht.", "tiktok_caption": "Wie?",
              "instagram_caption": "Wahnsinn.", "hashtags_youtube": ["clutch", "papaplatte"],
              "hashtags_tiktok": ["clutch"], "hashtags_instagram": ["clutch"]}
    monkeypatch.setitem(ai._CALLS, "fake", lambda settings, prompt, *a: prompts.append(prompt) or json.dumps(answer))
    monkeypatch.setattr(ai, "enabled", lambda settings: True)
    monkeypatch.setattr(ai, "generate_json", lambda settings, prompt, schema, system: prompts.append(prompt) or answer)
    texts = viral.write_texts(s(), channel(tags=["deutschestreamer"]), "Stream", "ich hab nur noch ein leben", 1,
                              "", "Papaplatte", "gaming", "Fortnite")
    assert "Art des Streams: Gaming (Fortnite)" in prompts[0] and "Spielmoment" in prompts[0]
    assert "#fortnite" in texts["platforms"]["tiktok"]["caption"] and "#deutschestreamer" in texts["platforms"]["tiktok"]["caption"]
    assert "#shorts" in texts["platforms"]["youtube"]["caption"]


def test_without_ai_the_caption_gets_kind_hashtags():
    texts = viral.write_texts(s(), channel(), "Stream", "was war das denn", 1, "", "Papaplatte", "irl", "IRL")
    assert texts["caption"].endswith("#irl #streamer")
    plain = viral.write_texts(s(), channel(caption_template="{hook} #meins"), "Stream", "hallo", 1, "", "", "irl", "IRL")
    assert plain["caption"] == "hallo #meins"  # eigene Hashtags in der Vorlage bleiben unangetastet


def test_live_clip_uses_the_layout_of_its_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(run.transcribe, "transcribe_range",
                        lambda path, a, b, *r: [Word(a + i * 0.4, a + i * 0.4 + 0.3, f"w{i}") for i in range(int((b - a) / 0.4))])
    used = []
    monkeypatch.setattr(run.render, "render_clip", lambda source, a, b, out, ass, layout, zoom:
                        used.append(layout) or out.write_bytes(b"x") or out)
    raw = tmp_path / "szene.mp4"
    st = State(tmp_path / "db.sqlite")
    creator = {"id": "papaplatte", "name": "Papaplatte", "url": "https://www.twitch.tv/papaplatte"}
    for i, cat in enumerate(("Just Chatting", "Fortnite")):
        raw.write_bytes(b"x")
        note = {"start": 15, "end": 55, "duration": 90, "score": 50, "base": 9, "mode": "chat", "category": cat}
        st.mark_video(f"cut{i}", "", "Stream", "queued", creator="papaplatte", url=str(raw), kind="livecut",
                      note=json.dumps(note))
        run.process_livecut([channel()], s(work_dir=tmp_path, live_compilation=False), st, dict(st.get_video(f"cut{i}")), creator)
    assert used == ["crop", "blur"]
    assert sorted(c["genre"] for c in st.clips()) == ["Gaming (Fortnite)", "Just Chatting"]


def test_live_session_reads_the_category(tmp_path, monkeypatch):
    monkeypatch.setattr(genre, "twitch_category", lambda login: "Just Chatting" if login == "papaplatte" else None)
    creator = {"id": "papaplatte", "name": "Papaplatte", "url": "https://www.twitch.tv/papaplatte"}
    session = live.Session(creator, creator["url"], "Chill", s(work_dir=tmp_path), lambda: State(tmp_path / "db.sqlite"),
                           recorder=object(), chat=lambda stop: None)
    session._update_category()
    status = {"category": session.category, "genre": live.genre.describe(live.genre.classify(session.category), session.category)}
    assert status == {"category": "Just Chatting", "genre": "Just Chatting"}


def test_layout_settings_are_checked():
    raw = {"settings": s(layout_irl="schief"), "channels": [channel()], "accounts": [], "creators": []}
    with pytest.raises(config.ConfigError):
        config.validate(raw)
