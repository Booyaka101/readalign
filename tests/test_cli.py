"""The command line surface: defaults, exit codes, and refusals that stay readable."""

import json
import os
import shutil
import zipfile

import pytest

from conftest import build_epub
from readalign.cli import build_parser, main
from test_epub import ENCRYPTION

has_ffmpeg = shutil.which("ffmpeg") and shutil.which("ffprobe")


def test_defaults_match_the_documented_ones():
    args = build_parser().parse_args(["build", "--audio", "a", "--epub", "b", "--out", "c"])
    assert args.model == "large-v3-turbo"
    assert args.drift_threshold == 2.5
    assert args.sidecars is True
    assert args.dry_run is False
    assert args.language is None


def test_no_sidecars_flag():
    args = build_parser().parse_args(
        ["build", "--audio", "a", "--epub", "b", "--out", "c", "--no-sidecars"]
    )
    assert args.sidecars is False


def test_missing_subcommand_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit):
        main([])
    assert "usage: readalign" in capsys.readouterr().err


def test_missing_epub_is_a_message_not_a_traceback(tmp_path, capsys):
    code = main(["build", "--audio", str(tmp_path), "--epub", str(tmp_path / "gone.epub"),
                 "--out", str(tmp_path / "out.epub"), "--dry-run"])
    assert code == 1
    assert "gone.epub" in capsys.readouterr().err


def test_drm_epub_exits_two(tmp_path, capsys):
    path = build_epub(str(tmp_path / "drm.epub"), extra={"META-INF/encryption.xml": ENCRYPTION})
    code = main(["build", "--audio", str(tmp_path), "--epub", path,
                 "--out", str(tmp_path / "out.epub"), "--dry-run"])
    assert code == 2
    assert "readalign does not handle DRM-protected files" in capsys.readouterr().err


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg is needed")
def test_drm_audio_exits_two(tmp_path, capsys):
    (tmp_path / "book.aax").write_bytes(b"not really an audible file")
    epub = build_epub(str(tmp_path / "book.epub"))
    code = main(["build", "--audio", str(tmp_path / "book.aax"), "--epub", epub,
                 "--out", str(tmp_path / "out.epub"), "--dry-run"])
    assert code == 2
    err = capsys.readouterr().err
    assert "DRM" in err
    assert "book.aax" in err


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg is needed")
def test_drm_file_in_an_audio_directory_is_named(tmp_path, capsys):
    from test_output import silent_mp3

    epub = build_epub(str(tmp_path / "book.epub"))
    audio = tmp_path / "audio"
    audio.mkdir()
    silent_mp3(audio / "part01.mp3", 5)
    (audio / "licence.acsm").write_bytes(b"a download ticket, not audio")
    code = main(["build", "--audio", str(audio), "--epub", epub,
                 "--out", str(tmp_path / "out.epub"), "--dry-run"])
    assert code == 2
    err = capsys.readouterr().err
    assert "licence.acsm" in err


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg is needed")
def test_dry_run_prints_a_plan_and_writes_nothing(tmp_path, capsys):
    from test_output import silent_mp3

    epub = build_epub(str(tmp_path / "book.epub"))
    audio = tmp_path / "audio"
    audio.mkdir()
    silent_mp3(audio / "part01.mp3", 5)
    out = tmp_path / "out.epub"
    code = main(["build", "--audio", str(audio), "--epub", epub, "--out", str(out), "--dry-run"])
    output = capsys.readouterr().out
    assert code == 0
    assert "dry run" in output
    assert "documents      2 of 2 spine items carry text" in output
    assert not out.exists()


def test_empty_audio_directory_is_a_clear_error(tmp_path, capsys):
    epub = build_epub(str(tmp_path / "book.epub"))
    empty = tmp_path / "empty"
    empty.mkdir()
    code = main(["build", "--audio", str(empty), "--epub", epub,
                 "--out", str(tmp_path / "out.epub"), "--dry-run"])
    assert code == 1
    assert "no audio files" in capsys.readouterr().err.lower()


def test_check_on_an_epub_without_overlays(tmp_path, capsys):
    epub = build_epub(str(tmp_path / "plain.epub"))
    code = main(["check", epub, "--no-audio-probe"])
    assert code == 1
    assert "no spine document has a media-overlay attribute" in capsys.readouterr().err


def _rewrite(path, changes):
    """Copy an EPUB with some entries replaced, keeping the stored mimetype first."""
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries.update(changes)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            if name == "mimetype":
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, data)
            else:
                archive.writestr(name, data)
    return path


def test_check_on_a_corrupt_container_is_one_line_not_a_traceback(tmp_path, capsys):
    path = str(tmp_path / "corrupt.epub")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml", b"<container version=1.0><oops")
    code = main(["check", path])
    assert code == 1
    err = capsys.readouterr().err
    assert "container.xml is not well-formed XML" in err
    assert "Traceback" not in err


def test_check_on_a_corrupt_package_document_is_one_line(tmp_path, capsys):
    path = build_epub(str(tmp_path / "corrupt-opf.epub"))
    path = _rewrite(path, {"EPUB/package.opf": b"<?xml version=1.0?><package><manifest>"})
    code = main(["check", path, "--no-audio-probe"])
    assert code == 1
    assert "package document EPUB/package.opf is not well-formed XML" in capsys.readouterr().err


def test_check_reports_a_broken_overlay_as_a_finding_not_a_crash(tmp_path, capsys):
    path = build_epub(str(tmp_path / "brokensmil.epub"))
    with zipfile.ZipFile(path) as archive:
        opf = archive.read("EPUB/package.opf")
    path = _rewrite(
        path,
        {
            "EPUB/package.opf": opf.replace(
                b'<item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>',
                b'<item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml" '
                b'media-overlay="ov1"/>',
            ).replace(
                b"</manifest>",
                b'<item id="ov1" href="ov1.smil" media-type="application/smil+xml"/></manifest>',
            ),
            "EPUB/ov1.smil": b"<smil xmlns=http://www.w3.org/ns/SMIL broken",
        },
    )
    code = main(["check", path, "--no-audio-probe"])
    assert code == 1
    assert "ov1.smil: the overlay is not well-formed XML" in capsys.readouterr().err


def test_negative_drift_threshold_is_refused(tmp_path, capsys):
    epub = build_epub(str(tmp_path / "book.epub"))
    code = main(["build", "--audio", str(tmp_path), "--epub", epub,
                 "--out", str(tmp_path / "out.epub"), "--drift-threshold", "-1"])
    assert code == 1
    assert "--drift-threshold" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0"])
def test_nonfinite_or_zero_drift_threshold_is_refused(tmp_path, capsys, value):
    code = main(["build", "--audio", "unused", "--epub", "unused",
                 "--out", str(tmp_path / "out.epub"), f"--drift-threshold={value}"])
    assert code == 1
    assert "finite positive" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-0.1", "1.1"])
def test_invalid_token_match_threshold_is_refused(tmp_path, capsys, value):
    code = main(["build", "--audio", "unused", "--epub", "unused",
                 "--out", str(tmp_path / "out.epub"), f"--min-token-match={value}"])
    assert code == 1
    assert "--min-token-match" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["hardlink", "symlink"])
def test_output_alias_cannot_overwrite_ebook(tmp_path, capsys, kind):
    epub = build_epub(str(tmp_path / "book.epub"))
    original = (tmp_path / "book.epub").read_bytes()
    out = tmp_path / "alias.epub"
    try:
        if kind == "hardlink":
            os.link(epub, out)
        else:
            out.symlink_to(epub)
    except OSError:
        pytest.skip(f"{kind} unavailable on this filesystem")
    code = main(["build", "--audio", "unused", "--epub", epub, "--out", str(out)])
    assert code == 1
    assert "overwrite your ebook" in capsys.readouterr().err
    assert (tmp_path / "book.epub").read_bytes() == original


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg is needed")
@pytest.mark.parametrize("threshold,expected", [("0", 0), ("0.5", 0), ("1", 5)])
def test_build_token_match_gate_keeps_inspectable_outputs(
    tmp_path, monkeypatch, capsys, threshold, expected,
):
    from conftest import speak
    from readalign.epub import load_epub
    from test_output import silent_mp3

    epub = build_epub(str(tmp_path / "book.epub"))
    package = load_epub(epub)
    words = speak([sentence for doc in package.docs for sentence in doc.sentences])
    # Omit one interior word, leaving enough anchors for a low-drift alignment.
    del words[len(words) // 2]
    audio = silent_mp3(tmp_path / "narration.mp3", words[-1].end + 2)
    monkeypatch.setattr(
        "readalign.asr.transcribe_tracks",
        lambda *a, **k: (words, {"cached_tracks": 0, "seconds": 0, "device": "test"}),
    )
    out = tmp_path / "out.epub"
    code = main(["build", "--audio", audio, "--epub", epub, "--out", str(out),
                 "--min-token-match", threshold])
    assert code == expected
    output = capsys.readouterr()
    if expected == 5:
        assert "--min-token-match" in output.err
    report = json.loads((tmp_path / "readalign-report.json").read_text(encoding="utf-8"))
    assert report["token_match_gate"] == {
        "threshold": float(threshold), "passed": expected == 0,
    }
    assert 0.5 < report["coverage"]["token_match_rate"] < 1
    assert out.is_file()
    assert (tmp_path / "out-sidecars" / "align.json").is_file()
    assert main(["check", str(out)]) == 0
