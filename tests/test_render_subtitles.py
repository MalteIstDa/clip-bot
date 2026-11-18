"""ffmpeg ohne libass: Clips entstehen ohne Untertitel statt abzustuerzen, ffmpeg-full wird bevorzugt."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from clipbot import render


def test_filter_without_subtitles_has_no_ass():
    assert "ass=" not in render._filter("blur", 1.4, None)
    assert "ass=subs.ass" in render._filter("crop", 1.4, "subs.ass")


def test_render_without_libass_drops_subtitles(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(render, "can_burn_subtitles", lambda: False)
    monkeypatch.setattr(render, "_warned", False)
    monkeypatch.setattr(render.subprocess, "run", fake_run)
    ass = tmp_path / "subs.ass"
    ass.write_text("x")
    render.render_clip(tmp_path / "in.mp4", 0, 5, tmp_path / "out.mp4", ass)
    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "ass=" not in graph


def test_render_with_libass_keeps_subtitles(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(render, "can_burn_subtitles", lambda: True)
    monkeypatch.setattr(render.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    ass = tmp_path / "subs.ass"
    ass.write_text("x")
    render.render_clip(tmp_path / "in.mp4", 0, 5, tmp_path / "out.mp4", ass, layout="crop")
    assert "ass=subs.ass" in calls[0][calls[0].index("-filter_complex") + 1]


def test_use_full_ffmpeg_puts_folder_first(tmp_path, monkeypatch):
    folder = tmp_path / "ffmpeg-full" / "bin"
    folder.mkdir(parents=True)
    (folder / "ffmpeg").write_text("")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert render.use_full_ffmpeg((str(tmp_path / "fehlt"), str(folder))) == str(folder)
    assert os.environ["PATH"].split(os.pathsep)[0] == str(folder)
    render.can_burn_subtitles.cache_clear()


def test_use_full_ffmpeg_without_folder(tmp_path):
    assert render.use_full_ffmpeg((str(tmp_path / "fehlt"),)) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg fehlt")
def test_can_burn_subtitles_reads_filter_list():
    render.can_burn_subtitles.cache_clear()
    assert isinstance(render.can_burn_subtitles(), bool)
