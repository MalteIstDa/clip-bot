"""TikTok-Fenster: Auswahl des Nutzers (Sichtbarkeit, Kommentare, Markeninhalte) geht genau so an TikTok."""
import pytest

import run
from clipbot import config
from clipbot.uploaders import tiktok
from clipbot.uploaders.base import UploadError

CREATOR = {"creator_username": "malte", "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
           "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False, "max_video_post_duration_sec": 600}


def test_automatic_posts_keep_the_settings():
    assert tiktok.post_options(CREATOR, None, "public") == {
        "privacy_level": "PUBLIC_TO_EVERYONE", "disable_duet": False, "disable_comment": False, "disable_stitch": False}
    assert tiktok.post_options({}, None, "public")["privacy_level"] == "SELF_ONLY"  # ungeprueft: nur privat


def test_users_choice_is_sent_and_disabled_features_stay_off():
    info = tiktok.post_options(CREATOR, {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True,
                                         "allow_duet": True, "allow_stitch": False, "brand_organic": True}, "private")
    assert info == {"privacy_level": "PUBLIC_TO_EVERYONE", "disable_comment": False, "disable_duet": True,
                    "disable_stitch": True, "brand_organic_toggle": True, "brand_content_toggle": False}


def test_privacy_must_be_chosen_and_branded_content_is_never_private():
    with pytest.raises(UploadError, match="wer das Video"):
        tiktok.post_options(CREATOR, {"privacy_level": ""}, "public")
    with pytest.raises(UploadError, match="Markeninhalte"):
        tiktok.post_options(CREATOR, {"privacy_level": "SELF_ONLY", "brand_content": True}, "public")


def test_tiktok_window_choice_reaches_the_upload(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text("settings: {work_dir: data}\n", encoding="utf-8")
    monkeypatch.setattr(app.autopilot, "check_now", lambda: None)
    client = app.app.test_client()
    headers = {"X-ClipBot": "1", "Host": "127.0.0.1:8765"}
    post = lambda url, payload=None: client.post(url, json=payload or {}, headers=headers)
    post("/api/accounts", {"platform": "tiktok", "label": "Main"})
    post("/api/channels", {"is_new": True, "name": "clips", "accounts": ["tt_main"]})
    st = app.open_state(config.load(allow_empty=True)["settings"])
    st.add_clip("a", "v", "clips", tmp_path / "a.mp4", "A", "Text #gtasty", 0, 30)
    st.close()
    jobs, sent = [], []
    monkeypatch.setattr(app, "start_job", lambda label, fn: jobs.append(fn) or True)
    monkeypatch.setattr(run, "upload_one", lambda account, clip, s, tags=None, tiktok=None:
                        sent.append((run.State.texts_for(clip, "tiktok")[1], tiktok)) or "pub1")
    assert post("/api/clips/a/upload", {"account": "tt_main", "tiktok": {"privacy_level": "nix"}}).status_code == 400
    r = post("/api/clips/a/upload", {"account": "tt_main", "tiktok": {
        "privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True, "caption": "Neu #gtasty"}})
    assert r.status_code == 200 and jobs[0]() is True
    assert sent == [("Neu #gtasty", {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True, "allow_duet": False,
                                     "allow_stitch": False, "brand_organic": False, "brand_content": False,
                                     "caption": "Neu #gtasty"})]
