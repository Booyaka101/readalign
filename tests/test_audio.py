"""Input discovery: patterns, directories, and the ordering between numbered files."""

import pytest

from readalign.audio import (
    _chapters_from,
    _duration_of,
    discover_inputs,
    natural_key,
    prepare_tracks,
)
from readalign.errors import DRMError, InputError


def test_natural_order_puts_part2_before_part10():
    names = ["part10.mp3", "part2.mp3", "part1.mp3"]
    assert sorted(names, key=natural_key) == ["part1.mp3", "part2.mp3", "part10.mp3"]


def test_a_glob_pattern_is_expanded_in_natural_order(tmp_path):
    """The shell does not expand patterns for a native command on Windows, so readalign must."""
    for name in ("part10.mp3", "part2.mp3", "part1.mp3"):
        (tmp_path / name).write_bytes(b"x")
    assert discover_inputs(str(tmp_path / "part*.mp3")) == [
        str(tmp_path / "part1.mp3"),
        str(tmp_path / "part2.mp3"),
        str(tmp_path / "part10.mp3"),
    ]


def test_a_glob_that_matches_nothing_is_a_clear_error(tmp_path):
    with pytest.raises(InputError, match="matched no audio files"):
        discover_inputs(str(tmp_path / "book-*.mp3"))


def test_a_glob_skips_files_that_are_not_audio(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "cover.jpg").write_bytes(b"x")
    assert discover_inputs(str(tmp_path / "*")) == [str(tmp_path / "a.mp3")]


def test_a_drm_file_a_glob_matches_is_refused(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.aax").write_bytes(b"x")
    with pytest.raises(DRMError, match=r"b\.aax"):
        discover_inputs(str(tmp_path / "*"))


@pytest.mark.parametrize("duration", ["inf", "nan", "-1", "N/A", {}])
def test_invalid_audio_duration_is_rejected(duration):
    probe = {"format": {"duration": duration}, "streams": [{"codec_type": "audio"}]}
    with pytest.raises(InputError, match="no readable duration"):
        _duration_of(probe, "audio.mp3")


def test_invalid_chapter_marks_are_ignored():
    probe = {"chapters": [
        {"start_time": "nan", "end_time": "inf"},
        {"start_time": "-1", "end_time": "2"},
        {"start_time": "0", "end_time": "3"},
    ]}
    assert len(_chapters_from(probe)) == 1


def test_work_directory_cannot_overwrite_any_source_audio(tmp_path, monkeypatch):
    from readalign import audio

    directory = tmp_path / "audio"
    directory.mkdir()
    source = directory / "part001.mp3"
    source.write_bytes(b"original")
    monkeypatch.setattr(audio, "ffprobe", lambda path: {
        "streams": [{"codec_type": "audio", "codec_name": "mp3"}],
    })
    with pytest.raises(InputError, match="overwrite source audio"):
        prepare_tracks([str(source)], str(tmp_path), "96k", log=lambda *_: None)
    assert source.read_bytes() == b"original"
