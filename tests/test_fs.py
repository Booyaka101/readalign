"""Destination replacement and shared collision handling."""

import pytest

from readalign.errors import InputError
from readalign.fs import unique_filenames, write_text
from readalign.members import SourceFile
from readalign.package import write_epub


@pytest.mark.parametrize("exists", [False, True])
def test_failed_epub_write_leaves_no_partial_output(tmp_path, exists):
    target = tmp_path / "out.epub"
    if exists:
        target.write_bytes(b"previous successful build")
    with pytest.raises(InputError, match="cannot write"):
        write_epub(str(target), {
            "mimetype": b"application/epub+zip",
            "audio/part.mp3": SourceFile(str(tmp_path / "missing.mp3")),
        }, [])
    if exists:
        assert target.read_bytes() == b"previous successful build"
    else:
        assert not target.exists()
    assert not list(tmp_path.glob(".readalign-*"))


def test_failed_text_replacement_preserves_previous_report(tmp_path, monkeypatch):
    from readalign import fs

    target = tmp_path / "report.json"
    target.write_text("previous", encoding="utf-8")

    def fail(*args):
        raise PermissionError("destination locked")

    monkeypatch.setattr(fs.os, "replace", fail)
    with pytest.raises(InputError, match="destination locked"):
        write_text(str(target), "new report")
    assert target.read_text(encoding="utf-8") == "previous"
    assert not list(tmp_path.glob(".readalign-*"))


def test_unique_names_preserve_uncontested_names_and_reserved_suffixes():
    names = ["chapter.vtt", "Chapter.vtt", "chapter.1.vtt", "other.vtt"]
    result = unique_filenames(names)
    assert len({name.casefold() for name in result}) == len(names)
    assert result[2:] == names[2:]
    assert unique_filenames(["chapter1.smil", "chapter2.smil"]) == [
        "chapter1.smil", "chapter2.smil",
    ]
