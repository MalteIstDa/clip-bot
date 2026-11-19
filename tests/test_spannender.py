"""Spannendere Clips: Facecam oben und Spiel unten, Punch-ins an lauten Stellen, Chat-Inhalt fuer die KI."""
import json
import subprocess

import pytest

from clipbot import bestof, config, facecam, live, render, subtitles
from clipbot.state import State
from clipbot.transcribe import Word


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return tuple(int(v) for v in out.split(","))


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    src = tmp_path_factory.mktemp("v") / "spiel.mp4"
    # 4 s Ton leise, dann 1 s laut (Schrei), dann wieder leise
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                    "-filter_complex", "[1:a]volume='if(between(t,5,6),1,0.03)':eval=frame[a]", "-map", "0:v",
                    "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    return src


# ---------------------------------------------------------------- Facecam finden
def test_facecam_is_a_small_face_in_the_same_place():
    face = (1060, 40, 90, 90)  # oben rechts in einem 1280x720-Bild
    frames = [[face], [(1062, 42, 88, 88)], [], [(1058, 39, 92, 92), (300, 400, 60, 60)], [face]]
    x, y, w, h = facecam.box_from_faces(frames, 1280, 720)
    assert x <= 1060 and x + w >= 1276 and y == 0  # Ecke oben rechts ganz mitgenommen
    assert abs(w / h - 16 / 9) < 0.05


def test_facecam_window_is_cut_along_its_frame():
    """Wie bei xRohat: Fenster oben rechts mit Rahmen links und unten, oben weicher Uebergang zum Spiel."""
    import numpy as np
    W, H = 1280, 720
    static = np.zeros((H, W), bool)
    static[90:275, 955] = True      # linke Rahmenkante
    static[273, 955:1280] = True    # untere Rahmenkante
    static[150:275, 1177] = True    # Tuerrahmen im Zimmer: steht auch, ist aber kein Rahmen
    motion = np.full((H, W), 20.0)  # das Spiel bewegt sich ueberall ...
    motion[90:274, 956:1280] = 2.0  # ... nur das Zimmer hinter dem Streamer kaum
    x, y, w, h = facecam.frame_box((static, motion), 1112, 216, 58, W, H)
    assert 955 <= x <= 962 and 86 <= y <= 98 and 1270 <= x + w <= 1280 and 265 <= y + h <= 273


def test_face_in_the_middle_without_frame_is_no_facecam():
    frames = [[(600, 300, 80, 80)]] * 6  # Spielfigur in einer Zwischensequenz
    assert facecam.box_from_faces(frames, 1280, 720) is None


def test_no_facecam_for_full_camera_or_random_faces():
    big = [[(400, 100, 420, 420)]] * 6  # Kamera fuellt das Bild (Just Chatting)
    assert facecam.box_from_faces(big, 1280, 720) is None
    jumping = [[(100 + 150 * i, 300, 60, 60)] for i in range(6)]  # Gesichter im Spiel, immer woanders
    assert facecam.box_from_faces(jumping, 1280, 720) is None
    assert facecam.box_from_faces([], 1280, 720) is None


def test_find_without_video_is_none(tmp_path):
    assert facecam.find(tmp_path / "fehlt.mp4", 0, 5) is None


# ---------------------------------------------------------------- Rendern
def test_facecam_layout_renders_cam_on_top(tmp_path, video):
    out = render.render_clip(video, 0, 4, tmp_path / "fc.mp4", None, "facecam", 1.2, False, (1000, 20, 270, 160))
    assert probe(out) == (1080, 1920)
    graph = render._filter("facecam", 1.2, "subs.ass", (1000, 20, 270, 160))
    assert "crop=270:160:1000:20" in graph and "vstack" in graph and graph.endswith("ass=subs.ass[v]")


def test_facecam_without_cam_falls_back_to_whole_picture(tmp_path, video, monkeypatch):
    monkeypatch.setattr(facecam, "find", lambda *a: None)
    assert render.prepare_layout(video, 0, 4, "facecam") == ("blur", None)
    assert render.prepare_layout(video, 0, 4, "crop") == ("crop", None)
    assert probe(render.render_clip(video, 0, 4, tmp_path / "b.mp4", None, "facecam", 1.2)) == (1080, 1920)


def test_punch_in_at_the_loud_spot(tmp_path, video):
    spots = render.loud_spots(video, 0, 10)
    assert spots and 4.5 <= spots[0][0] <= 5.5
    out = render.render_clip(video, 0, 10, tmp_path / "p.mp4", None, "crop", 1.2, True)
    assert probe(out) == (1080, 1920)
    assert "zoompan" in render._filter("crop", 1.2, None, None, spots)


def test_hook_moves_below_the_facecam(tmp_path):
    ass = subtitles.write_ass([Word(0, 0.5, "hallo")], 0, 3, tmp_path / "s.ass", hook="Krass",
                              hook_margin=render.hook_margin("facecam", (0, 0, 320, 180)))
    assert ",8,90,90,556,1" in ass.read_text()  # 16:9-Kamera: Streifen 606 px hoch, Hook knapp darueber
    assert render.cam_height((0, 0, 300, 200)) == 720 and render.cam_height((0, 0, 100, 200)) == 900


def test_settings_allow_facecam():
    raw = {"settings": {**config.DEFAULTS, "layout_chatting": "facecam"}, "channels": []}
    config.validate(raw, allow_empty=True)
    assert config.DEFAULTS["layout_gaming"] == "facecam" and config.DEFAULTS["punch_ins"] is True


# ---------------------------------------------------------------- Chat fuer die KI
def test_chat_summary_counts_the_reactions():
    texts = ["KEKW"] * 5 + ["KEKW KEKW KEKW"] * 3 + ["W"] * 4 + ["bro what"] + [""]
    assert live.chat_summary(texts) == "KEKW (8x), W (4x), bro what"


def test_scene_note_carries_the_chat(tmp_path):
    class Rec:
        def cut(self, start, end, out):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x")
            return out, start, end - start
    creator = {"id": "lacy", "name": "Lacy", "url": "https://www.twitch.tv/lacy"}
    session = live.Session(creator, creator["url"], "GTA", {**config.DEFAULTS, "work_dir": tmp_path},
                           lambda: State(tmp_path / "db.sqlite"), recorder=Rec(), chat=lambda stop: None)
    for i in range(6):
        session.on_message(1000 + i, "LMAO")
    session.on_message(900, "alt")  # vor dem Peak: zaehlt nicht
    session._cut({"ts": 1000, "at": 1008, "score": 40, "base": 5, "laughs": 6})
    st = State(tmp_path / "db.sqlite")
    row = [r for r in st.db.execute("SELECT * FROM videos WHERE kind='livecut'")][0]
    assert json.loads(row["note"])["chat"] == "LMAO (6x)"


def test_ai_sees_the_chat_of_live_moments(tmp_path, monkeypatch):
    prompts = []
    monkeypatch.setattr(bestof.ai, "enabled", lambda s: True)
    monkeypatch.setattr(bestof.ai, "generate_json", lambda s, prompt, schema, system: prompts.append(prompt) or
                        {"picks": [{"index": 0, "headline": "Lacy rastet aus"}], "youtube_title": "x",
                         "youtube_description": "y", "tiktok_caption": "z", "hashtags": ["gta6"]})
    moments = [{"moment_id": "live-1", "creator": "lacy", "creator_name": "Lacy", "video_title": "GTA",
                "duration": 70, "score": 5, "text": "Chat beim Peak: KEKW (40x)\nGesagt: no way"}]
    bestof.plan({**config.DEFAULTS}, {"name": "c", "language": "de", "tags": []}, moments, "daily", "Lacy: Beste")
    assert "Chat beim Peak: KEKW (40x)" in prompts[0] and "Zuschauer reagiert" in prompts[0]


def test_punch_in_keeps_picture_and_sound_together(tmp_path):
    """Twitch sendet oft 60 Bilder pro Sekunde: Das Bild darf durch den Zoom nicht laenger werden als der Ton."""
    src = tmp_path / "s60.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=60:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)
    out = tmp_path / "out.mp4"
    render.render_clip(src, 0, 6, out, None, "crop", 1.2, False)
    graph_out = tmp_path / "zoom.mp4"
    import clipbot.render as r
    original = r.loud_spots
    try:
        r.loud_spots = lambda *a: [(2.0, 3.2)]
        render.render_clip(src, 0, 6, graph_out, None, "facecam", 1.2, True, (0, 0, 320, 180))
    finally:
        r.loud_spots = original
    for path in (out, graph_out):
        lengths = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=duration", "-of", "csv=p=0",
                                  str(path)], capture_output=True, text=True).stdout.split()
        video, audio = (float(v) for v in lengths[:2])
        assert abs(video - audio) < 0.15, (path.name, video, audio)
