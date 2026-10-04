"""The command line surface: defaults, exit codes, and refusals that stay readable."""

import shutil

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


def test_negative_drift_threshold_is_refused(tmp_path, capsys):
    epub = build_epub(str(tmp_path / "book.epub"))
    code = main(["build", "--audio", str(tmp_path), "--epub", epub,
                 "--out", str(tmp_path / "out.epub"), "--drift-threshold", "-1"])
    assert code == 1
    assert "--drift-threshold" in capsys.readouterr().err
