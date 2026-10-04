"""Input discovery: patterns, directories, and the ordering between numbered files."""

import pytest

from readalign.audio import discover_inputs, natural_key
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
