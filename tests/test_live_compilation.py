"""Live-Zusammenschnitt: Chat-Peaks werden kurze Momente, die besten eines Streamers ergeben einen Clip ueber 1 Minute."""
import json
import subprocess

import pytest

import run
from clipbot import bestof, config, notify
from clipbot.state import State
from clipbot.transcribe import Word


def s(**kw):
    return {**config.DEFAULTS, **kw}


def channel(**kw):
    return {"name": "clips", "kind": "clips", "language": "de", "creators": ["lacy"], "accounts": [], "tags": [],
            "credit": "", "title_template": "{hook} #shorts", "caption_template": "{hook}", "clips": True,
            "bestof": "off", "distribute": "alle", **kw}


def fake_words(path, a, b, *rest):
    a = max(0.0, a)
    return [Word(a + i * 0.4, a + i * 0.4 + 0.3, f"w{i}." if i % 6 == 5 else f"w{i}") for i in range(int((b - a) / 0.4))]


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    src = tmp_path_factory.mktemp("szene") / "szene.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=72",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=72", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    return src


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(notify, "_push", lambda *a: None)
    monkeypatch.setattr(run.transcribe, "transcribe_range", fake_words)
    monkeypatch.setattr(bestof.transcribe, "transcribe_range", fake_words)


def queue_scene(st, tmp_path, scene, n, score):
    raw = tmp_path / f"szene{n}.mp4"
    raw.write_bytes(scene.read_bytes())
    note = {"start": 10, "end": 62, "duration": 72, "peak": 50, "score": score, "base": 10, "laughs": n,
            "mode": "chat", "stream_title": "GTA RP", "category": "Grand Theft Auto VI"}
    cut_id = f"live-lacy-1-{n:05d}"
    st.mark_video(cut_id, "", "GTA RP", "queued", creator="lacy", url=str(raw), kind="livecut", note=json.dumps(note))
    return dict(st.get_video(cut_id))


def test_live_window_lies_mostly_before_the_peak():
    words = fake_words(None, 0, 72)
    start, end = bestof.live_window(words, 50, 72, 20)
    assert 31 <= start <= 37 and 49 <= end <= 58 and 14 <= end - start <= 25


def test_live_window_stays_inside_the_scene():
    start, end = bestof.live_window([], 5, 30, 20)
    assert start == 0 and end == 20


def test_peaks_become_moments_until_one_clip_over_a_minute(tmp_path, scene):
    st = State(tmp_path / "db.sqlite")
    cfg_s = s(work_dir=tmp_path)
    creator = {"id": "lacy", "name": "Lacy", "url": "https://www.twitch.tv/lacy"}
    made = [run.process_livecut([channel()], cfg_s, st, queue_scene(st, tmp_path, scene, n, 30 + 10 * n), creator)
            for n in range(1, 6)]
    assert made[:3] == [0, 0, 0] and sum(made) == 1  # erst ab etwa 85 s Material wird ausgewaehlt
    clip = st.clips()[0]
    length = clip["end"] - clip["start"]
    assert clip["review"] == "vault" and clip["kind"] == "live" and clip["creator"] == "Lacy"
    assert 60.5 <= length <= 90 and clip["genre"] == "Gaming (Grand Theft Auto VI)"
    assert "Chat-Peaks" in clip["reason"] and "#grandtheftautovi" in clip["caption"].lower()
    real = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 clip["path"]], capture_output=True, text=True).stdout)
    assert real >= 60
    left = bestof.live_moments(st, "clips", "lacy")
    assert 1 <= len(left) <= 2  # uebrige Momente warten auf den naechsten Clip
    assert st.vault_counts() == {"clips": 1}
    assert not any((tmp_path / f"szene{n}.mp4").exists() for n in range(1, 6))


def test_moments_of_different_streamers_are_not_mixed(tmp_path, scene):
    st = State(tmp_path / "db.sqlite")
    cfg_s = s(work_dir=tmp_path)
    for n, who in enumerate(("lacy", "xqc", "lacy", "xqc", "lacy", "xqc"), 1):
        row = queue_scene(st, tmp_path, scene, n, 50)
        run.process_livecut([channel(creators=["lacy", "xqc"])], cfg_s, st, row, {"id": who, "name": who})
    assert st.clips() == []
    assert len(bestof.live_moments(st, "clips", "lacy")) == 3 and len(bestof.live_moments(st, "clips", "xqc")) == 3


def test_scene_cap_counts_finished_clips_when_compiling():
    from clipbot import live
    session = live.Session.__new__(live.Session)
    session.s = s(live_max_clips_per_stream=4)
    assert session.max_scenes() == 20
    session.s = s(live_max_clips_per_stream=4, live_compilation=False)
    assert session.max_scenes() == 4


def test_settings_reject_bad_compilation_lengths():
    with pytest.raises(config.ConfigError):
        config.validate({"settings": {**config.DEFAULTS, "live_compilation_min_seconds": 90,
                                      "live_compilation_max_seconds": 60}, "channels": []}, allow_empty=True)
