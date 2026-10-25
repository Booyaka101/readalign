"""Input discovery: patterns, directories, and the ordering between numbered files."""

import shutil
import subprocess

import pytest

from readalign.audio import (
    _chapters_from,
    _duration_of,
    _output_plan,
    discover_inputs,
    ffprobe,
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


@pytest.mark.parametrize("codec,container,expected", [
    ("mp3", "mp3", (".mp3", "audio/mpeg", False)),
    ("aac", "mov,mp4,m4a,3gp,3g2,mj2", (".m4a", "audio/mp4", False)),
    ("opus", "ogg", (".opus", "audio/ogg; codecs=opus", False)),
    ("flac", "flac", (".m4a", "audio/mp4", True)),
])
def test_output_format_defaults_preserve_existing_behavior(codec, container, expected):
    probe = {"streams": [{"codec_type": "audio", "codec_name": codec}],
             "format": {"format_name": container}}
    assert _output_plan("source", probe) == expected
    assert _output_plan("source", probe, "mp3") == (".mp3", "audio/mpeg", codec != "mp3")


def test_unknown_audio_format_is_rejected(tmp_path):
    with pytest.raises(InputError, match="audio format"):
        prepare_tracks([], str(tmp_path), "96k", audio_format="wav")


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="ffmpeg and ffprobe are needed")
@pytest.mark.parametrize("source_codec,suffix", [("aac", ".m4a"), ("libmp3lame", ".mp3")])
def test_mp3_mode_prepares_real_audio_without_changing_source(tmp_path, source_codec, suffix):
    source = tmp_path / ("narration" + suffix)
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=2", "-c:a", source_codec, str(source),
    ], check=True)
    original = source.read_bytes()
    tracks = prepare_tracks([str(source)], str(tmp_path / "work"), "96k",
                            log=lambda *_: None, audio_format="mp3")
    track, = tracks
    assert track.media_type == "audio/mpeg"
    assert track.path.endswith(".mp3")
    assert track.transcoded == (source_codec != "libmp3lame")
    assert 1.9 < track.duration < 2.2
    assert ffprobe(track.path)["streams"][0]["codec_name"] == "mp3"
    assert source.read_bytes() == original


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="ffmpeg and ffprobe are needed")
def test_mp3_conversion_downmixes_surround_and_retains_chapters(tmp_path):
    source = tmp_path / "surround.m4a"
    metadata = tmp_path / "chapters.txt"
    metadata.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\n"
                        "title=First\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=2000\n"
                        "title=Second\n", encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=5.1", "-i", str(metadata), "-map_metadata", "1",
        "-t", "2", "-c:a", "aac", str(source),
    ], check=True)
    original = source.read_bytes()
    track, = prepare_tracks([str(source)], str(tmp_path / "work"), "96k",
                            log=lambda *_: None, audio_format="mp3")
    probe = ffprobe(track.path)
    assert probe["streams"][0]["codec_name"] == "mp3"
    assert probe["streams"][0]["channels"] == 2
    assert [chapter.title for chapter in track.chapters] == ["First", "Second"]
    assert track.chapters[1].start == pytest.approx(1, abs=0.05)
    assert source.read_bytes() == original
