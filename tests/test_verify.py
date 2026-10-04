"""The optional epubcheck pass: finding the tool, reading its output, and the exit code."""

import json
import shutil

import pytest

from conftest import build_epub, speak
from readalign import verify
from readalign.errors import DependencyError

has_ffmpeg = shutil.which("ffmpeg") and shutil.which("ffprobe")


def test_epubcheck_output_is_counted():
    output = (
        "Validating using EPUB version 3.3 ruleset.\n"
        "ERROR(RSC-005): something is wrong\n"
        "WARNING(OPF-001): something is odd\n"
        "Messages: 1 warnings / 1 errors\n"
    )
    assert verify.parse_epubcheck(output) == (1, 1)
    assert verify.parse_epubcheck("") == (0, 0)


def test_an_explicit_jar_that_is_missing_is_refused(tmp_path):
    with pytest.raises(DependencyError, match="not there"):
        verify.find_epubcheck(str(tmp_path / "nope.jar"))


def test_an_explicit_jar_is_used_as_given(tmp_path):
    jar = tmp_path / "epubcheck.jar"
    jar.write_bytes(b"")
    assert verify.find_epubcheck(str(jar)) == str(jar)


def test_a_jar_is_found_in_epubcheck_home(tmp_path, monkeypatch):
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    monkeypatch.setenv("EPUBCHECK_HOME", str(tmp_path))
    jar = tmp_path / "epubcheck-5.2.1.jar"
    jar.write_bytes(b"")
    assert verify.find_epubcheck("auto") == str(jar)


def test_no_epubcheck_anywhere_is_a_dependency_error(tmp_path, monkeypatch):
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    for name in ("EPUBCHECK_HOME", "JAVA_HOME", "PATH"):
        monkeypatch.setenv(name, str(tmp_path))
    with pytest.raises(DependencyError, match="epubcheck"):
        verify.find_epubcheck("auto")


class _Result:
    def __init__(self, returncode, output):
        self.returncode = returncode
        self.stdout = output
        self.stderr = ""


def test_a_failed_run_is_reported_not_raised(monkeypatch, capsys):
    monkeypatch.setattr(
        verify.subprocess, "run",
        lambda command, **_: _Result(1, "ERROR(RSC-005): something is wrong\n"),
    )
    verdict = verify.run_epubcheck("out.epub", "epubcheck.jar", log=print)
    assert verdict == {
        "tool": "epubcheck.jar", "exit_code": 1, "errors": 1, "warnings": 0, "ok": False,
    }
    assert "epubcheck" in capsys.readouterr().out


def test_a_clean_run_is_ok(monkeypatch):
    monkeypatch.setattr(
        verify.subprocess, "run",
        lambda command, **_: _Result(0, "epubcheck completed successfully\n"),
    )
    assert verify.run_epubcheck("out.epub", "epubcheck.jar", log=lambda *_: None)["ok"]


def fake_build(monkeypatch, tmp_path, *, verify_result):
    """A full run of run_build with the recogniser and epubcheck stubbed out."""
    from readalign import asr
    from readalign.cli import main
    from test_output import silent_mp3

    epub_path = build_epub(str(tmp_path / "in.epub"))
    audio = silent_mp3(tmp_path / "part001.mp3", 90)

    def transcribe(tracks, **_):
        from readalign.epub import load_epub

        package = load_epub(epub_path)
        sentences = [sentence for doc in package.docs for sentence in doc.sentences]
        info = {"device": "cpu", "compute_type": "int8", "model": "tiny.en",
                "cached_tracks": 0, "language": "en", "words": 0, "seconds": 0.1}
        return speak(sentences), info

    monkeypatch.setattr(asr, "transcribe_tracks", transcribe)
    monkeypatch.setattr(verify, "find_epubcheck", lambda explicit: "epubcheck.jar")
    monkeypatch.setattr(verify, "run_epubcheck", lambda *a, log=print, **k: verify_result)
    return main(
        ["build", "--audio", str(audio), "--epub", epub_path,
         "--out", str(tmp_path / "out.epub"), "--verify", "--no-sidecars",
         "--no-cache", "--device", "cpu"]
    )


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg is needed to prepare real audio")
def test_an_epubcheck_failure_changes_the_exit_code(monkeypatch, tmp_path, capsys):
    code = fake_build(
        monkeypatch, tmp_path,
        verify_result={"tool": "epubcheck.jar", "exit_code": 1, "errors": 2,
                       "warnings": 0, "ok": False},
    )
    assert code == 4
    assert "epubcheck" in capsys.readouterr().out


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg is needed to prepare real audio")
def test_a_clean_epubcheck_pass_lands_in_the_report(monkeypatch, tmp_path):
    code = fake_build(
        monkeypatch, tmp_path,
        verify_result={"tool": "epubcheck.jar", "exit_code": 0, "errors": 0,
                       "warnings": 0, "ok": True},
    )
    assert code == 0
    report = json.loads((tmp_path / "readalign-report.json").read_text(encoding="utf-8"))
    assert report["verify"]["ok"] is True
